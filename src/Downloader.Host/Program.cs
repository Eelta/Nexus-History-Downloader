using System.Net;
using System.Text;
using System.Text.Json;
using CustomFileDownloader.Core;

namespace CustomFileDownloader.DownloaderHost;

/// <summary>
/// 下载宿主 —— 融合模式的核心进程（纯控制台，日志打印在启动它的 cmd 窗口里）：
/// 1) 以 HTTP 服务接收来自 Nexus 仪表盘（Python/FastAPI + Playwright 接管窗口）
///    转交的下载请求 {url, cookie, referrer, filename}；
/// 2) 用 CustomFileDownloader 引擎（256 线程分段下载）逐个执行；
/// 3) 进度写入控制台与 cache/takeover-jobs.jsonl（仪表盘 /api/takeover/status 读取）。
/// 默认监听 http://127.0.0.1:18765/，可用环境变量 CUSTOMDL_HOST_PORT 修改。
/// 控制台输出刻意保持纯 ASCII，避免中文代码页乱码。
/// </summary>
internal static class Program
{
    private const string Version = "1.0.0";
    private const int DefaultPort = 18765;
    // 实测（本机 64MB 回环 + Python 服务器）8/16/32/64/128/256 六档中 8~64 最快，
    // 256 明显变慢（服务器端瓶颈）；而 Nexus CDN 单连接限速场景下高并发仍有意义。
    // 权衡后默认取 64（APP 可设 CUSTOMDL_MAX_THREADS 覆盖）。
    private const int DefaultMaxThreads = 64;
    private const int MaxParallelJobs = 5;
    private const long MaxBodyBytes = 1024 * 1024;
    private const double MaxCorsAgeSeconds = 86400;

    private static readonly object Sync = new();
    private static readonly List<JobSnapshot> Jobs = new();
    private static readonly Dictionary<string, CancellationTokenSource> JobCtss = new();
    // 并行下载任务数：默认 5（环境变量 CUSTOMDL_MAX_JOBS 可调 1~16），超出排队
    private static readonly SemaphoreSlim WorkerGate = new(JobSlots(), JobSlots());
    private static readonly string RootDir = Path.GetFullPath(Environment.CurrentDirectory);
    private static readonly string CacheDir = Path.Combine(RootDir, "cache");
    private static readonly string SettingsPath = Path.Combine(RootDir, "settings.json");
    private static string DefaultDir = CacheDir;
    private static int _seq;
    private static readonly string StartedAt = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");

    /// <summary>当前最大并发线程数：可运行时调整（/api/settings 或环境变量初始值）。</summary>
    private static volatile int MaxThreads = EnvMaxThreads();

    private static int Port
    {
        get
        {
            var raw = Environment.GetEnvironmentVariable("CUSTOMDL_HOST_PORT");
            return int.TryParse(raw, out var p) && p is > 0 and < 65536 ? p : DefaultPort;
        }
    }

    private static int EnvMaxThreads()
    {
        var raw = Environment.GetEnvironmentVariable("CUSTOMDL_MAX_THREADS");
        return int.TryParse(raw, out var v) && v is > 0 and <= 4096 ? v : DefaultMaxThreads;
    }

    /// <summary>并行下载任务数：环境变量 CUSTOMDL_MAX_JOBS（默认 5，范围 1~16）。</summary>
    private static int JobSlots()
    {
        var raw = Environment.GetEnvironmentVariable("CUSTOMDL_MAX_JOBS");
        return int.TryParse(raw, out var v) && v is >= 1 and <= 16 ? v : MaxParallelJobs;
    }

    private static string BaseUrl => $"http://127.0.0.1:{Port}/";

    private static async Task<int> Main()
    {
        Console.OutputEncoding = Encoding.UTF8;
        LoadSettings();
        Directory.CreateDirectory(CacheDir);

        Console.WriteLine($"CustomFileDownloader engine host v{Version}");
        Console.WriteLine($"  HTTP endpoint : {BaseUrl}  (env CUSTOMDL_HOST_PORT to change)");
        Console.WriteLine($"  default save  : {DefaultDir}");
        Console.WriteLine($"  max threads   : {MaxThreads}  (env CUSTOMDL_MAX_THREADS / 页面滑块可调, default 64)");
        Console.WriteLine($"  parallel jobs : {JobSlots()}  (env CUSTOMDL_MAX_JOBS, default 5, 超出排队)");
        Console.WriteLine($"  job log       : {Path.Combine(CacheDir, "takeover-jobs.jsonl")}");
        Console.WriteLine("Waiting for takeover requests from the dashboard window... (Ctrl+C to exit)");
        Console.WriteLine();

        using var listener = new HttpListener();
        listener.Prefixes.Add(BaseUrl);
        listener.Start();
        try
        {
            while (true)
            {
                var ctx = await listener.GetContextAsync();
                _ = Task.Run(() => HandleAsync(ctx));
            }
        }
        finally
        {
            listener.Stop();
        }
    }

    // ---------------------------------------------------------------- settings

    private sealed class AppSettingsJson
    {
        public string? DefaultDownloadDir { get; set; }
    }

    private static void LoadSettings()
    {
        try
        {
            if (File.Exists(SettingsPath))
            {
                var s = JsonSerializer.Deserialize<AppSettingsJson>(File.ReadAllText(SettingsPath));
                if (!string.IsNullOrWhiteSpace(s?.DefaultDownloadDir))
                    DefaultDir = Path.GetFullPath(s.DefaultDownloadDir);
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine($"settings.json read failed (using default dir): {ex.Message}");
        }
    }

    /// <summary>每次下载前重读 settings.json 的默认目录（改目录即时生效）。</summary>
    private static string? ReadSettingsDir()
    {
        try
        {
            if (File.Exists(SettingsPath))
            {
                var s = JsonSerializer.Deserialize<AppSettingsJson>(File.ReadAllText(SettingsPath));
                if (!string.IsNullOrWhiteSpace(s?.DefaultDownloadDir))
                    return Path.GetFullPath(s.DefaultDownloadDir);
            }
        }
        catch { /* 读失败用启动时的默认目录 */ }
        return null;
    }

    // ------------------------------------------------------------------- http

    private static async Task HandleAsync(HttpListenerContext ctx)
    {
        var resp = ctx.Response;
        AddCors(ctx);
        try
        {
            if (ctx.Request.HttpMethod == "OPTIONS")
            {
                resp.StatusCode = 204;
                resp.Close();
                return;
            }
            var path = ctx.Request.Url?.AbsolutePath ?? "/";
            if (ctx.Request.HttpMethod == "GET" && path == "/api/status")
            {
                WriteJson(resp, new
                {
                    ok = true,
                    version = Version,
                    port = Port,
                    saveDir = DefaultDir,
                    startedAt = StartedAt,
                    maxThreads = MaxThreads,
                    jobs = RecentJobs(),
                });
                return;
            }
            if (ctx.Request.HttpMethod == "POST" && path == "/api/settings")
            {
                await HandleSettingsAsync(ctx, resp).ConfigureAwait(false);
                return;
            }
            if (ctx.Request.HttpMethod == "POST" && path == "/api/cancel")
            {
                await HandleCancelAsync(ctx, resp).ConfigureAwait(false);
                return;
            }
            if (ctx.Request.HttpMethod == "POST" && path == "/api/takeover")
            {
                await HandleTakeoverAsync(ctx, resp).ConfigureAwait(false);
                return;
            }
            WriteJson(resp, new { ok = false, message = "unknown path" }, 404);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[HTTP] error: {ex.Message}");
            try { WriteJson(resp, new { ok = false, message = ex.Message }, 500); } catch { /* client gone */ }
        }
    }

    private static async Task<string> ReadBodyAsync(HttpListenerContext ctx)
    {
        using var reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8);
        var sb = new StringBuilder();
        var buf = new char[8192];
        int n;
        while ((n = await reader.ReadAsync(buf, 0, buf.Length).ConfigureAwait(false)) > 0)
        {
            sb.Append(buf, 0, n);
            if (sb.Length > MaxBodyBytes) throw new InvalidDataException("request body too large");
        }
        return sb.ToString();
    }

    private static async Task HandleCancelAsync(HttpListenerContext ctx, HttpListenerResponse resp)
    {
        var body = await ReadBodyAsync(ctx).ConfigureAwait(false);
        string jobId;
        using (var doc = JsonDocument.Parse(body))
        {
            jobId = doc.RootElement.TryGetProperty("jobId", out var t) ? t.GetString() ?? "" : "";
        }
        if (string.IsNullOrWhiteSpace(jobId))
            throw new InvalidDataException("缺少 jobId");
        CancellationTokenSource? cts = null;
        lock (Sync)
        {
            JobCtss.TryGetValue(jobId.Trim(), out cts);
        }
        if (cts is null)
            throw new InvalidDataException($"任务不存在或已完成：{jobId}");
        cts.Cancel();
        Console.WriteLine($"[CANCEL] job {jobId} -> cancel requested");
        WriteJson(resp, new { ok = true, message = "已请求取消" });
    }

    private static async Task HandleSettingsAsync(HttpListenerContext ctx, HttpListenerResponse resp)
    {
        var body = await ReadBodyAsync(ctx).ConfigureAwait(false);
        using var doc = JsonDocument.Parse(body);
        bool has = doc.RootElement.TryGetProperty("maxThreads", out var t);
        int v = 0;
        if (has) has = t.TryGetInt32(out v);
        if (!has || v is < 1 or > 4096)
            throw new InvalidDataException("maxThreads 需为 1~4096 的整数");
        MaxThreads = v;
        Console.WriteLine($"[SET] max threads -> {MaxThreads}");
        WriteJson(resp, new { ok = true, maxThreads = MaxThreads });
    }

    private static async Task HandleTakeoverAsync(HttpListenerContext ctx, HttpListenerResponse resp)
    {
        var body = await ReadBodyAsync(ctx).ConfigureAwait(false);

        var req = NativeJson.Deserialize<TakeoverRequest>(body)
                  ?? throw new InvalidDataException("cannot parse request body");
        if (req.Type != "download" || string.IsNullOrWhiteSpace(req.Url))
            throw new InvalidDataException("missing url");

        var snap = Enqueue(req);
        Console.WriteLine($"[Takeover #{snap.Id}] enqueued: {req.Url}");
        Console.WriteLine($"[Takeover #{snap.Id}] save path : {snap.SavePath}");
        WriteJson(resp, new TakeoverResponse { Ok = true, Message = "enqueued", JobId = snap.Id });
    }

    // ------------------------------------------------------------------- jobs

    private static JobSnapshot Enqueue(TakeoverRequest req)
    {
        JobSnapshot snap;
        var cts = new CancellationTokenSource();
        // 每任务线程数：请求级 maxThreads 覆盖 > 全局（滑块/环境变量）
        var jobThreads = req.MaxThreads is > 0 and <= 4096 ? req.MaxThreads.Value : MaxThreads;
        lock (Sync)
        {
            _seq++;
            var id = $"T{_seq:000}";
            var savePath = ResolveSavePath(req);
            snap = new JobSnapshot
            {
                Id = id,
                Url = req.Url,
                Filename = Path.GetFileName(savePath),
                SavePath = savePath,
                State = "queued",
                StartedAt = Now(),
            };
            Jobs.Add(snap);
            JobCtss[id] = cts;
            AppendJsonl(snap);
        }
        _ = RunJobAsync(snap, req, cts, jobThreads);
        return snap;
    }

    private static async Task RunJobAsync(JobSnapshot snap, TakeoverRequest req,
        CancellationTokenSource cts, int jobThreads)
    {
        await WorkerGate.WaitAsync().ConfigureAwait(false);
        SegmentedDownloader? dl = null;
        JobSnapshot current = snap;   // 最新快照：进度事件基于它链式更新，避免状态被写回 queued
        try
        {
            if (cts.IsCancellationRequested)
            {
                current = current with { State = "canceled", FinishedAt = Now() };
                SetSnapshot(current);
                Console.WriteLine($"[Takeover #{snap.Id}] cancelled while queued");
                return;
            }
            current = current with { State = "downloading" };
            SetSnapshot(current);
            Console.WriteLine($"[Takeover #{snap.Id}] downloading ({jobThreads}-thread engine)...");

            var headers = new Dictionary<string, string>();
            if (!string.IsNullOrWhiteSpace(req.Cookie)) headers["Cookie"] = req.Cookie;
            if (!string.IsNullOrWhiteSpace(req.Referrer)) headers["Referer"] = req.Referrer;
            if (!string.IsNullOrWhiteSpace(req.UserAgent)) headers["User-Agent"] = req.UserAgent;

            dl = new SegmentedDownloader(new DownloadOptions
            {
                OverwriteExisting = true,
                SimulateBrowserHeaders = headers.ContainsKey("Cookie"),
                Headers = headers.Count > 0 ? headers : null,
                RootDirectory = RootDir,
                CacheDirectory = CacheDir,
                MaxThreads = jobThreads,   // 任务级线程数（未指定时=全局滑块值）
                AlwaysFillThreads = true,
            });

            long lastConsoleTick = 0;
            dl.ProgressChanged += (_, e) =>
            {
                current = current with
                {
                    Downloaded = e.DownloadedBytes,
                    Total = e.TotalBytes,
                    Speed = e.SpeedBytesPerSecond,
                    ActiveThreads = e.ActiveThreads,
                    StartedThreads = e.StartedThreads,
                };
                SetSnapshot(current);
                var tick = Environment.TickCount64;
                if (tick - lastConsoleTick < 2000) return;
                lastConsoleTick = tick;
                var pct = e.TotalBytes is > 0 ? $"{e.Progress * 100:F1}%" : FormatBytes(e.DownloadedBytes);
                Console.WriteLine($"[Takeover #{snap.Id}] {pct} | {FormatSpeed(e.SpeedBytesPerSecond)} | active {e.ActiveThreads} (started {e.StartedThreads})");
            };

            var result = await dl.DownloadAsync(req.Url, snap.SavePath, cts.Token);
            var finalPath = CorrectFileNameIfMeaningless(snap.Id, snap.SavePath, dl.HeaderFileName);
            current = current with
            {
                State = "done",
                Downloaded = result.TotalBytes,
                SavePath = finalPath,
                Filename = Path.GetFileName(finalPath),
                AvgSpeedBps = result.ElapsedSeconds > 0 ? result.TotalBytes / result.ElapsedSeconds : 0,
                PeakSpeedBps = result.PeakSpeedBytesPerSecond,
                ElapsedSeconds = result.ElapsedSeconds,
                MaxConcurrent = result.MaxConcurrentThreads,
                FinishedAt = Now(),
            };
            SetSnapshot(current);
            Console.WriteLine($"[Takeover #{snap.Id}] done: {FormatBytes(result.TotalBytes)}, {result.ElapsedSeconds:F1}s, " +
                              $"max concurrency {result.MaxConcurrentThreads}, multithread {(result.UsedMultiThread ? "yes" : "no")} -> {finalPath}");
        }
        catch (OperationCanceledException)
        {
            current = current with { State = "canceled", FinishedAt = Now() };
            SetSnapshot(current);
            Console.WriteLine($"[Takeover #{snap.Id}] cancelled (temp files cleaned)");
        }
        catch (Exception ex)
        {
            current = current with { State = "error", Error = ex.Message, FinishedAt = Now() };
            SetSnapshot(current);
            Console.WriteLine($"[Takeover #{snap.Id}] error: {ex.Message}");
        }
        finally
        {
            lock (Sync)
            {
                JobCtss.Remove(snap.Id);
            }
            cts.Dispose();
            dl?.Dispose();
            WorkerGate.Release();
        }
    }

    /// <summary>文件名无扩展名（如签名 URL 的 UUID 令牌名）时，用响应头里的真实文件名修正。</summary>
    private static string CorrectFileNameIfMeaningless(string jobId, string savePath, string? headerName)
    {
        try
        {
            var cur = Path.GetFileName(savePath);
            if (string.IsNullOrEmpty(headerName) || !string.IsNullOrEmpty(Path.GetExtension(cur)))
                return savePath;
            var dir = Path.GetDirectoryName(savePath) ?? ".";
            var newPath = Path.Combine(dir, SanitizeFileName(headerName) ?? headerName);
            if (string.Equals(Path.GetFullPath(newPath), Path.GetFullPath(savePath), StringComparison.OrdinalIgnoreCase))
                return savePath;
            File.Move(savePath, newPath, overwrite: true);
            Console.WriteLine($"[Takeover #{jobId}] filename fixed by Content-Disposition: {Path.GetFileName(newPath)}");
            return newPath;
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[Takeover #{jobId}] rename failed: {ex.Message}");
            return savePath;
        }
    }

    private static string ResolveSavePath(TakeoverRequest req)
    {
        // 优先级：本次任务指定目录 > settings.json 默认目录 > 启动时默认（根 cache）
        var dir = ReadSettingsDir() ?? DefaultDir;
        if (!string.IsNullOrWhiteSpace(req.SaveDir))
            dir = Path.GetFullPath(req.SaveDir);
        var name = SanitizeFileName(req.Filename) ?? SuggestFileName(req.Url);
        Directory.CreateDirectory(dir);
        return UniquePathInActiveJobs(dir, name);
    }

    /// <summary>
    /// 同名文件唯一化：若该保存路径正被「排队中/下载中」的任务占用，
    /// 自动追加 " (1)"、" (2)"…（避免并发/排队下载同一个文件时互相覆盖）。
    /// 磁盘上已存在的旧成品仍按原语义覆盖。
    /// </summary>
    private static string UniquePathInActiveJobs(string dir, string name)
    {
        var candidate = Path.Combine(dir, name);
        if (!ActiveJobTakesPath(candidate)) return candidate;
        var stem = Path.GetFileNameWithoutExtension(name);
        var ext = Path.GetExtension(name);
        for (var i = 1; i <= 50; i++)
        {
            var alt = Path.Combine(dir, $"{stem} ({i}){ext}");
            if (!ActiveJobTakesPath(alt)) return alt;
        }
        return Path.Combine(dir, $"{stem} ({Guid.NewGuid():N}){ext}");
    }

    private static bool ActiveJobTakesPath(string path)
    {
        var full = Path.GetFullPath(path);
        lock (Sync)
        {
            foreach (var j in Jobs)
            {
                if ((j.State is "queued" or "downloading") &&
                    string.Equals(Path.GetFullPath(j.SavePath), full, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
        }
        return false;
    }

    private static string? SanitizeFileName(string? name)
    {
        if (string.IsNullOrWhiteSpace(name)) return null;
        var invalid = Path.GetInvalidFileNameChars();
        var sb = new StringBuilder(name.Length);
        foreach (var ch in name)
            sb.Append(Array.IndexOf(invalid, ch) >= 0 ? '_' : ch);
        var result = sb.ToString().Trim();
        return string.IsNullOrEmpty(result) || result is "." or ".." ? null : result;
    }

    private static string SuggestFileName(string url)
    {
        if (Uri.TryCreate(url.Trim(), UriKind.Absolute, out var uri))
        {
            var name = Uri.UnescapeDataString(Path.GetFileName(uri.AbsolutePath));
            if (!string.IsNullOrEmpty(name) && name.Length <= 180)
                return name;
        }
        return $"download_{DateTime.Now:yyyyMMdd_HHmmss}.bin";
    }

    private static void SetSnapshot(JobSnapshot snap)
    {
        lock (Sync)
        {
            var i = Jobs.FindIndex(j => j.Id == snap.Id);
            if (i >= 0) Jobs[i] = snap;
        }
        AppendJsonl(snap);
    }

    private static JobSnapshot[] RecentJobs()
    {
        // 过滤掉无意义文件名（签名 URL 的 UUID 令牌名）的任务：不参与仪表盘展示
        lock (Sync) return Jobs.Where(j => !IsJunkFileName(j.Filename)).TakeLast(20).Reverse().ToArray();
    }

    private static bool IsJunkFileName(string? filename)
    {
        var baseName = Path.GetFileNameWithoutExtension(filename ?? "");
        if (baseName.Length < 30) return false;
        return baseName.All(ch => Uri.IsHexDigit(ch) || ch == '-');
    }

    private static void AppendJsonl(JobSnapshot snap)
    {
        try
        {
            var path = Path.Combine(CacheDir, "takeover-jobs.jsonl");
            if (File.Exists(path) && new FileInfo(path).Length > 2 * 1024 * 1024)
            {
                // keep the tail so the file never grows without bound
                File.WriteAllLines(path, File.ReadLines(path).TakeLast(200));
            }
            File.AppendAllText(path, JsonSerializer.Serialize(snap, NativeJson.Options) + Environment.NewLine);
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[log] jsonl write failed: {ex.Message}");
        }
    }

    // ------------------------------------------------------------------- misc

    private static string Now() => DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");

    private static void AddCors(HttpListenerContext ctx)
    {
        // Local tool: only the dashboard origin may call the host cross-site;
        // any other page (e.g. a malicious website) gets no CORS permission.
        var origin = ctx.Request.Headers["Origin"] ?? "";
        if (origin.Length == 0 || origin == "http://127.0.0.1:8000")
        {
            ctx.Response.Headers["Access-Control-Allow-Origin"] = origin.Length == 0 ? "*" : origin;
            ctx.Response.Headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS";
            ctx.Response.Headers["Access-Control-Allow-Headers"] = "Content-Type";
            ctx.Response.Headers["Access-Control-Max-Age"] = MaxCorsAgeSeconds.ToString();
        }
    }

    private static void WriteJson(HttpListenerResponse resp, object obj, int statusCode = 200)
    {
        var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(obj, NativeJson.Options));
        resp.StatusCode = statusCode;
        resp.ContentType = "application/json; charset=utf-8";
        resp.ContentLength64 = bytes.Length;
        resp.OutputStream.Write(bytes, 0, bytes.Length);
        resp.Close();
    }

    private static string FormatBytes(long bytes) => bytes switch
    {
        >= 1024L * 1024 * 1024 => $"{bytes / 1024.0 / 1024 / 1024:F2} GB",
        >= 1024L * 1024 => $"{bytes / 1024.0 / 1024:F1} MB",
        >= 1024L => $"{bytes / 1024.0:F1} KB",
        _ => $"{bytes} B",
    };

    private static string FormatSpeed(double bytesPerSecond)
    {
        if (bytesPerSecond <= 0) return "0 B/s";
        return bytesPerSecond >= 1024L * 1024
            ? $"{bytesPerSecond / 1024 / 1024:F2} MB/s"
            : $"{bytesPerSecond / 1024:F1} KB/s";
    }
}
using System.Net;
using System.Net.Http.Headers;

namespace CustomFileDownloader.Core;

/// <summary>一次下载的最终结果。</summary>
public sealed class DownloadResult
{
    /// <summary>已保存到本地的完整路径。</summary>
    public required string FilePath { get; init; }
    /// <summary>文件总大小（未知时为实际已下载字节数）。</summary>
    public long TotalBytes { get; init; }
    /// <summary>耗时（秒）。</summary>
    public double ElapsedSeconds { get; init; }
    /// <summary>同时活动的最大连接数。</summary>
    public int MaxConcurrentThreads { get; init; }
    /// <summary>是否实际采用了多线程分段下载。</summary>
    public bool UsedMultiThread { get; init; }
    /// <summary>是否发生了“服务器不支持 Range → 自动降级单线程”。</summary>
    public bool UsedRangeFallback { get; init; }
    /// <summary>峰值下载速度（字节/秒）。</summary>
    public long PeakSpeedBytesPerSecond { get; init; }
    /// <summary>计划的分段总数（1 = 不分段）。</summary>
    public int TotalSegments { get; init; }
}

/// <summary>下载引擎参数（默认值与 PCL 2.13 的下载引擎一致）。</summary>
public sealed class DownloadOptions
{
    /// <summary>全局最大并发连接数（默认 256：默认即“拉满”取向；可用编程方式调整）。</summary>
    public int MaxThreads { get; init; } = 256;
    /// <summary>
    /// 极速模式（默认开启）：忽略“速度够高就不再追加线程”的自适应策略，
    /// 直接把所有分片×线程铺满（受 MaxThreads 与 256KB 最小分片限制）。
    /// </summary>
    public bool AlwaysFillThreads { get; init; } = true;
    /// <summary>最小分片大小，小于该值的碎片不再拆分（PCL FilePieceLimit = 256KB）。</summary>
    public long MinPieceSize { get; init; } = 256 * 1024L;
    /// <summary>小于该大小的文件不分段（PCL IsNoSplit = 1MB）。</summary>
    public long MinSplitSize { get; init; } = 1024 * 1024L;
    /// <summary>速度上限，-1 = 不限速（PCL ToolDownloadSpeed 默认 42 = 不限速）。</summary>
    public long SpeedLimitBytesPerSecond { get; init; } = -1;
    /// <summary>单次读取无数据超时（PCL 15~30s 自适应，这里取固定 30s）。</summary>
    public TimeSpan NoDataTimeout { get; init; } = TimeSpan.FromSeconds(30);
    /// <summary>慢连接判定：距上次收到数据超过该时长且数据量小于经过毫秒数时断开（PCL 5s/1KB/s）。</summary>
    public TimeSpan SlowConnectionTimeout { get; init; } = TimeSpan.FromSeconds(5);
    /// <summary>目标文件已存在时是否直接覆盖。</summary>
    public bool OverwriteExisting { get; init; } = true;
    /// <summary>是否模拟浏览器 User-Agent（部分下载源需要，如某些 CDN）。</summary>
    public bool SimulateBrowserHeaders { get; init; }
    /// <summary>
    /// 自定义请求头（如 Cookie / Referer），浏览器转交下载时使用。
    /// 引擎会对每个分片连接与降级单线程连接统一附加；Cookie 等受限头以不校验方式写入。
    /// </summary>
    public IReadOnlyDictionary<string, string>? Headers { get; init; }
    /// 沙箱根目录：仅用于约束缓存目录的位置（中间产物不越界）。
    /// 成品保存位置不受限制，可由调用方任意指定。
    /// 默认 = 当前工作目录（Environment.CurrentDirectory）。
    /// </summary>
    public string? RootDirectory { get; init; }
    /// <summary>
    /// 缓存目录：所有中间产物（分片临时文件、合并/降级临时文件）的统一存放位置。
    /// 默认 = &lt;RootDirectory&gt;/cache。必须位于 RootDirectory 之内。
    /// </summary>
    public string? CacheDirectory { get; init; }
}

/// <summary>进度事件。</summary>
public sealed class DownloadProgressEventArgs : EventArgs
{
    /// <summary>已下载字节数。</summary>
    public long DownloadedBytes { get; init; }
    /// <summary>文件总大小；未知时为 null。</summary>
    public long? TotalBytes { get; init; }
    /// <summary>当前速度（字节/秒）。</summary>
    public double SpeedBytesPerSecond { get; init; }
    /// <summary>当前活动连接数。</summary>
    public int ActiveThreads { get; init; }
    /// <summary>累计启动过的连接数。</summary>
    public int StartedThreads { get; init; }
    /// <summary>0~1 进度。</summary>
    public double Progress => TotalBytes is > 0 ? Math.Clamp(DownloadedBytes / (double)TotalBytes.Value, 0, 1) : 0;
}

/// <summary>
/// 多线程分段下载引擎。
/// 设计参考 PCL2 开源实现 Plain Craft Launcher 2/Modules/Base/ModNet.vb 的“多线程下载引擎”：
/// 首个连接不带 Range 探测文件大小；之后按需追加连接，每个连接用 HTTP Range 拉取文件的不同片段；
/// 服务器不支持 Range 时自动降级为单线程整文件下载。
/// </summary>
public sealed class SegmentedDownloader : IDisposable
{
    public event EventHandler<DownloadProgressEventArgs>? ProgressChanged;
    public event EventHandler<string>? LogMessage;

    /// <summary>首个响应中的 Content-Disposition 文件名（CDN 给出的真实文件名；可为 null）。</summary>
    public string? HeaderFileName { get; private set; }

    private const long InitialSpeedLow = 256 * 1024L;          // 与 PCL NetTaskSpeedLimitLow 一致
    private const int MaxRespawnPerChunk = 5;                  // 每个分片最多自动重连次数
    private static readonly TimeSpan SchedulerInterval = TimeSpan.FromMilliseconds(20);
    private const int ReadBufferSize = 16384;

    private readonly DownloadOptions _opt;
    private readonly HttpClient _http;
    private readonly CancellationTokenSource _lifetime = new();
    private readonly object _sync = new();
    private readonly List<Chunk> _chunks = new();
    private readonly List<Task> _workers = new();
    private readonly List<string> _temps = new();
    private readonly List<double> _speedSamples = new();

    private CancellationTokenSource _chunkCts = new();
    private string _url = string.Empty;
    private string _localPath = string.Empty;
    private string _cacheDir = string.Empty;
    private long _fileSize = -1;
    private bool _unknownSize;
    private long _doneBytes;
    private long _rateLeft;
    private long _speedLow = InitialSpeedLow;
    private long _peakSpeed;
    private double _currentSpeed;
    private int _activeThreads;
    private int _peakActiveThreads;
    private int _startedThreads;
    private int _nextChunkToStart;
    private long _idleStart;
    private int _started;
    private bool _rangeUnsupported;
    private bool _fallbackFinished;
    private string? _fatalError;

    public SegmentedDownloader(DownloadOptions? options = null)
    {
        _opt = options ?? new DownloadOptions();
        _http = new HttpClient(new HttpClientHandler { AllowAutoRedirect = true })
        {
            // 超时按“每次读取”单独控制（NoDataTimeout），与 PCL 的自适应超时思路一致
            Timeout = Timeout.InfiniteTimeSpan,
        };
    }

    /// <summary>下载单个文件。同一实例只能使用一次。</summary>
    public async Task<DownloadResult> DownloadAsync(string url, string localPath, CancellationToken cancellationToken = default)
    {
        if (Interlocked.Exchange(ref _started, 1) == 1)
            throw new InvalidOperationException("每个 SegmentedDownloader 实例只能下载一次。");
        url = url.Trim();
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) ||
            (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
            throw new ArgumentException("下载链接必须是 http/https 地址。", nameof(url));
        if (string.IsNullOrWhiteSpace(localPath))
            throw new ArgumentException("保存路径不能为空。", nameof(localPath));

        _url = uri.AbsoluteUri;
        _localPath = Path.GetFullPath(localPath);

        // 缓存沙箱：中间产物统一放 <根>/cache，不越界；成品保存位置不限
        var rootDir = Path.GetFullPath(_opt.RootDirectory ?? Environment.CurrentDirectory);
        _cacheDir = Path.GetFullPath(_opt.CacheDirectory ?? Path.Combine(rootDir, "cache"));
        if (!IsWithin(rootDir, _cacheDir))
            throw new ArgumentException($"缓存目录（{_cacheDir}）必须位于当前目录（{rootDir}）之内。");
        if (string.IsNullOrEmpty(Path.GetFileName(_localPath)))
            throw new ArgumentException("请输入包含文件名的完整保存路径。");
        Directory.CreateDirectory(_cacheDir);

        var dir = Path.GetDirectoryName(_localPath);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        if (File.Exists(_localPath))
        {
            if (!_opt.OverwriteExisting) throw new IOException($"目标文件已存在：{_localPath}");
            File.Delete(_localPath);
        }
        CleanTemps();

        var watch = System.Diagnostics.Stopwatch.StartNew();
        using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(_lifetime.Token, cancellationToken);
        var ct = linkedCts.Token;
        _chunkCts = CancellationTokenSource.CreateLinkedTokenSource(ct);

        try
        {
            var first = new Chunk { Index = 0, Start = 0, End = long.MaxValue, TempPath = TempPathFor(0) };
            lock (_sync) _chunks.Add(first);
            StartWorker(first, resume: false, ct);

            var monitor = Task.Run(() => MonitorLoopAsync(ct), CancellationToken.None);
            await monitor.ConfigureAwait(false);

            // 结果裁定
            if (ct.IsCancellationRequested)
            {
                await StopWorkersQuietlyAsync().ConfigureAwait(false);
                CleanTemps();
                throw new OperationCanceledException("下载已取消。", cancellationToken);
            }
            if (_fatalError != null)
            {
                await StopWorkersQuietlyAsync().ConfigureAwait(false);
                CleanTemps();
                throw new Exception(_fatalError);
            }

            if (_rangeUnsupported)
            {
                await FallbackSingleThreadAsync(ct).ConfigureAwait(false);
            }
            else if (_unknownSize)
            {
                // 服务器未给出大小：全程单连接流式下载，直接落盘
                MoveIntoPlace(_chunks[0].TempPath);
                CleanTemps();
            }
            else
            {
                if (!await TryMergeOrderedAsync(ct).ConfigureAwait(false))
                {
                    Log("多次合并失败，降级为单线程重新下载。");
                    CleanTemps();
                    await FallbackSingleThreadAsync(ct).ConfigureAwait(false);
                }
                else
                {
                    CleanTemps();
                }
            }

            watch.Stop();
            bool multi = _chunks.Count > 1 && !_unknownSize && !_rangeUnsupported;
            return new DownloadResult
            {
                FilePath = _localPath,
                TotalBytes = _fileSize > 0 ? _fileSize : Volatile.Read(ref _doneBytes),
                ElapsedSeconds = watch.Elapsed.TotalSeconds,
                MaxConcurrentThreads = _peakActiveThreads,
                UsedMultiThread = multi,
                UsedRangeFallback = _fallbackFinished || _rangeUnsupported,
                PeakSpeedBytesPerSecond = _peakSpeed,
                TotalSegments = _chunks.Count,
            };
        }
        catch (OperationCanceledException)
        {
            CleanTemps();
            throw;
        }
        catch
        {
            CleanTemps();
            throw;
        }
        finally
        {
            linkedCts.Cancel();          // 停掉监控循环
            await StopWorkersQuietlyAsync().ConfigureAwait(false);
        }
    }

    // ---------------------------------------------------------------
    // 监控循环：调度线程 + 采样速度 + 限速补充 + 进度上报
    // ---------------------------------------------------------------
    private async Task MonitorLoopAsync(CancellationToken ct)
    {
        long lastStatDone = 0;
        long lastStatTime = Environment.TickCount64;
        int tick = 0;
        while (!ct.IsCancellationRequested)
        {
            SchedulerTick(ct);
            tick++;
            if (tick % 5 == 0) // 约每 100ms
            {
                RefillRateLimit();
                SampleSpeed(ref lastStatDone, ref lastStatTime);
                RaiseProgress();
            }
            if (EvaluateTerminal()) return;
            try { await Task.Delay(SchedulerInterval, ct).ConfigureAwait(false); }
            catch (OperationCanceledException) { return; }
        }
    }

    private void SchedulerTick(CancellationToken ct)
    {
        if (_rangeUnsupported || _fatalError != null) return;
        lock (_sync)
        {
            if (_chunks.Count == 0) return;

            // 首连接尚未探测到文件大小（或文件大小未知的单流模式）：失败则从头重试
            if (_nextChunkToStart == 0 || _unknownSize)
            {
                var c0 = _chunks[0];
                if (!c0.Faulted || c0.Finished) return;
                if (c0.RespawnCount >= MaxRespawnPerChunk)
                {
                    _fatalError = "无法连接服务器或服务器无响应。";
                    return;
                }
                c0.Faulted = false;
                c0.RespawnCount++;
                if (_unknownSize && c0.Done > 0)
                {
                    // 大小未知时无法断点续传，从头重下
                    c0.Done = 0;
                    CleanTempFile(c0.TempPath);
                }
                StartWorker(c0, resume: false, ct);
                return;
            }

            // 1) 优先恢复失败的分片（从断点继续）
            foreach (var c in _chunks)
            {
                if (c.Faulted && !c.Finished)
                {
                    c.Faulted = false;
                    if (c.RespawnCount >= MaxRespawnPerChunk)
                    {
                        _fatalError = $"分片 {c.Index} 多次重试仍失败，下载终止。";
                        return;
                    }
                    c.RespawnCount++;
                    _idleStart = 0;
                    StartWorker(c, resume: true, ct);
                    return;
                }
            }

            // 2) 追加新分片：普通模式在速度低于自适应阈值时逐步加线程；
            //    极速模式忽略阈值，一次性铺满全部计划分片（尽量吃满带宽）
            while (_nextChunkToStart < _chunks.Count && _activeThreads < _opt.MaxThreads)
            {
                if (!_opt.AlwaysFillThreads && Volatile.Read(ref _currentSpeed) >= _speedLow) return;
                var c = _chunks[_nextChunkToStart++];
                _idleStart = 0;
                StartWorker(c, resume: false, ct);
                if (!_opt.AlwaysFillThreads) return; // 普通模式每 tick 只加一个线程
            }
        }
    }

    private bool EvaluateTerminal()
    {
        lock (_sync)
        {
            if (_fatalError != null || _rangeUnsupported) return true;
            if (_chunks.Count == 0) return false; // 首连接还在探测大小
            if (_unknownSize) return _chunks[0].Finished;
            if (_chunks.All(c => c.Finished)) return true;
            // 死等保护：所有连接都结束后仍未完成 → 判定失败（给重试留时间）
            if (_activeThreads == 0)
            {
                if (_idleStart == 0) _idleStart = Environment.TickCount64;
                else if (Environment.TickCount64 - _idleStart > 5000)
                {
                    _fatalError ??= "下载中断：所有连接均已断开且无法自动恢复。";
                    return true;
                }
            }
            else _idleStart = 0;
        }
        return false;
    }

    private void SampleSpeed(ref long lastDone, ref long lastTime)
    {
        long now = Environment.TickCount64;
        long dt = now - lastTime;
        if (dt < 90) return;
        long done = Volatile.Read(ref _doneBytes);
        double instant = (done - lastDone) * 1000.0 / dt;
        lastDone = done;
        lastTime = now;
        lock (_sync)
        {
            _speedSamples.Add(instant);
            if (_speedSamples.Count > 30) _speedSamples.RemoveAt(0);
            // 加权平均（近的权重高），与 PCL 的 30 次加权记速一致
            double acc = 0, wsum = 0;
            for (int i = 0; i < _speedSamples.Count; i++) { acc += _speedSamples[i] * (i + 1); wsum += i + 1; }
            Volatile.Write(ref _currentSpeed, wsum > 0 ? acc / wsum : 0);
            // 速度下限只升不降（PCL：取最近 1 秒平均的 85%）
            if (_speedSamples.Count >= 10)
            {
                double last10 = 0;
                for (int i = _speedSamples.Count - 10; i < _speedSamples.Count; i++) last10 += _speedSamples[i];
                last10 /= 10;
                long candidate = (long)(last10 * 0.85);
                if (candidate > _speedLow) _speedLow = candidate;
            }
        }
        if (instant > _peakSpeed) _peakSpeed = (long)instant;
    }

    private void RefillRateLimit()
    {
        long limit = _opt.SpeedLimitBytesPerSecond;
        if (limit <= 0) return;
        Interlocked.Add(ref _rateLeft, Math.Max(1, limit / 10)); // 每 100ms 补充 1/10（PCL 同款）
    }

    private void RaiseProgress()
    {
        try
        {
            ProgressChanged?.Invoke(this, new DownloadProgressEventArgs
            {
                DownloadedBytes = Volatile.Read(ref _doneBytes),
                TotalBytes = _fileSize > 0 ? _fileSize : null,
                SpeedBytesPerSecond = Volatile.Read(ref _currentSpeed),
                ActiveThreads = Volatile.Read(ref _activeThreads),
                StartedThreads = _startedThreads,
            });
        }
        catch { /* UI 抛出异常不应中断下载 */ }
    }

    // ---------------------------------------------------------------
    // 分片工作线程
    // ---------------------------------------------------------------
    private void StartWorker(Chunk c, bool resume, CancellationToken ct)
    {
        Interlocked.Increment(ref _activeThreads);
        _startedThreads++;
        if (_activeThreads > _peakActiveThreads) _peakActiveThreads = _activeThreads;
        var task = Task.Run(async () =>
        {
            try
            {
                await RunChunkAsync(c, resume, ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                /* 用户取消：由 DownloadAsync 的统一清理流程处理 */
            }
            catch (RangeNotSupportedException ex)
            {
                Log($"服务器不支持分段下载（{ex.Message}），将自动降级为单线程。");
                lock (_sync) _rangeUnsupported = true;
                _chunkCts.Cancel(); // 让其它分片尽快停止
            }
            catch (Exception ex)
            {
                if (ex.Message.Contains("磁盘空间不足"))
                {
                    lock (_sync) _fatalError = ex.Message;
                    _chunkCts.Cancel();
                    Log($"致命错误：{ex.Message}");
                    return;
                }
                c.Faulted = true;
                Log($"分片 {c.Index} 出错：{ex.Message}");
            }
            finally
            {
                Interlocked.Decrement(ref _activeThreads);
            }
        }, CancellationToken.None);
        lock (_sync) _workers.Add(task);
    }

    private async Task RunChunkAsync(Chunk c, bool resume, CancellationToken ct)
    {
        bool isFirstRequest = c.Index == 0 && !resume && c.Done == 0;
        using var req = new HttpRequestMessage(HttpMethod.Get, _url);
        ApplyHeaders(req);
        if (!isFirstRequest)
        {
            long from = c.Start + c.Done;
            long to = c.End;
            req.Headers.Range = new RangeHeaderValue(from, to >= from ? to : null);
        }
        Log($"分片 {c.Index} 建立连接：{(_opt.SimulateBrowserHeaders ? "模拟浏览器UA" : "默认UA")} Range={req.Headers.Range?.ToString() ?? "无(探测)"}");

        using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false);
        if ((int)resp.StatusCode == 416) throw new RangeNotSupportedException("HTTP 416");
        if ((int)resp.StatusCode >= 400) throw new HttpRequestException($"HTTP {(int)resp.StatusCode}");

        long contentLength = resp.Content.Headers.ContentLength ?? -1;

        if (isFirstRequest)
        {
            TryCaptureHeaderFileName(resp);
            if (resp.StatusCode == HttpStatusCode.PartialContent)
            {
                // 罕见：服务器在无 Range 时也回 206，尝试从 Content-Range 解析总大小
                long? total = ParseContentRangeTotal(resp.Content.Headers.ContentRange);
                if (total is > 0)
                {
                    _fileSize = total.Value;
                    BuildPlan();
                    c.End = ChunkEndFor(0);
                }
                else
                {
                    _unknownSize = true;
                }
            }
            else if (contentLength < 0)
            {
                _unknownSize = true;
            }
            else
            {
                _fileSize = contentLength;
                BuildPlan();
                c.End = ChunkEndFor(0);
            }
        }
        else
        {
            // 非首连接必须得到期望的分片（206），否则视为不支持 Range
            if (resp.StatusCode != HttpStatusCode.PartialContent)
                throw new RangeNotSupportedException($"服务器返回 {(int)resp.StatusCode} 而非 206");
            long expected = c.End - (c.Start + c.Done) + 1;
            if (contentLength >= 0 && contentLength != expected)
                throw new RangeNotSupportedException($"Content-Length={contentLength}，期望={expected}");
        }

        // 目标文件大小已知后的磁盘空间检查（对照 PCL：>50MB 时检查）
        if (_fileSize > 0 && _fileSize > 50L * 1024 * 1024 && c.Index == 0)
        {
            try
            {
                var drive = new DriveInfo(Path.GetPathRoot(_localPath) ?? Path.GetPathRoot(Path.GetTempPath()) ?? "");
                long required = _fileSize + 5L * 1024 * 1024;
                if (drive.IsReady && drive.TotalFreeSpace < required)
                    throw new IOException($"磁盘空间不足：需要约 {required / 1024 / 1024}MB，剩余 {drive.TotalFreeSpace / 1024 / 1024}MB");
            }
            catch (IOException) { throw; }
            catch { /* 取不到盘符信息时忽略 */ }
        }

        await using var src = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
        await using var dst = OpenChunkStream(c);
        var buf = new byte[ReadBufferSize];
        c.LastReceiveTime = Environment.TickCount64;

        while (!ct.IsCancellationRequested)
        {
            if (!_unknownSize && c.Done >= c.End - c.Start + 1) { c.Finished = true; break; }

            await ApplyRateLimitAsync(ct).ConfigureAwait(false);
            int n;
            try
            {
                n = await ReadWithTimeoutAsync(src, buf, ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (OperationCanceledException)
            {
                throw new TimeoutException($"分片 {c.Index} 读取超时（无数据超过 {_opt.NoDataTimeout.TotalSeconds:0}s）。");
            }
            if (n <= 0)
            {
                if (!_unknownSize && c.Done < c.End - c.Start + 1)
                    throw new IOException($"分片 {c.Index} 提前中断：服务器返回的数据不足。");
                c.Finished = true;
                break;
            }

            // 分片边界校准：单次读取可能越过本分片终点（探测连接返回整文件流、
            // 或 TCP 分段让最后一次 Read 跨过边界），截断到 End 处，保证合并时
            // 各分片大小精确一致（否则合并大小校验失败会被迫降级单线程）。
            if (!_unknownSize && c.Done + n > c.End - c.Start + 1)
                n = checked((int)(c.End - c.Start + 1 - c.Done));

            // 慢连接检测（对照 PCL：数据包间隔 >5s 且速度 <1KB/s 且非单线程 → 断开重连）
            if (_chunks.Count > 1 && c.LastReceiveTime > 0)
            {
                long since = Environment.TickCount64 - c.LastReceiveTime;
                if (since > (long)_opt.SlowConnectionTimeout.TotalMilliseconds && n < since && c.Done > 0)
                    throw new SlowConnectionException($"分片 {c.Index} 速度过慢（{n} B / {since} ms）。");
            }

            dst.Write(buf, 0, n);
            c.Done += n;
            Interlocked.Add(ref _doneBytes, n);
            if (_opt.SpeedLimitBytesPerSecond > 0) Interlocked.Add(ref _rateLeft, -n);
            c.LastReceiveTime = Environment.TickCount64;

            if (!_unknownSize && c.Done >= c.End - c.Start + 1) { c.Finished = true; break; }
        }
        Log($"分片 {c.Index} 结束：已下载 {c.Done} 字节。");
    }

    /// <summary>
    /// 附加请求头：优先使用调用方提供的自定义头（如浏览器会话 Cookie / Referer），
    /// 再按 SimulateBrowserHeaders 补默认浏览器 UA。每个分片连接与降级连接都会调用。
    /// </summary>
    /// <summary>记录首个响应头里的真实文件名（Content-Disposition），供宿主修正无意义文件名。</summary>
    private void TryCaptureHeaderFileName(HttpResponseMessage resp)
    {
        try
        {
            var cd = resp.Content.Headers.ContentDisposition?.FileName?.Trim();
            if (string.IsNullOrWhiteSpace(cd) || HeaderFileName is not null) return;
            var name = cd.Replace("\"", "").Trim();
            if (name.Length <= 200 && !name.Contains('/') && !name.Contains('\\'))
                HeaderFileName = name;
        }
        catch { /* 头解析失败不影响下载 */ }
    }

    private void ApplyHeaders(HttpRequestMessage req)
    {
        var custom = _opt.Headers;
        if (custom is { Count: > 0 })
        {
            foreach (var (name, value) in custom)
            {
                if (string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(value)) continue;
                if (name.Equals("User-Agent", StringComparison.OrdinalIgnoreCase))
                    req.Headers.UserAgent.TryParseAdd(value);
                else
                    req.Headers.TryAddWithoutValidation(name, value);
            }
        }
        if (_opt.SimulateBrowserHeaders && req.Headers.UserAgent.Count == 0)
            req.Headers.UserAgent.ParseAdd("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36");
    }

    private FileStream OpenChunkStream(Chunk c)
    {
        if (c.Done == 0)
            return new FileStream(c.TempPath, FileMode.Create, FileAccess.Write, FileShare.Read);
        return new FileStream(c.TempPath, FileMode.Append, FileAccess.Write, FileShare.Read);
    }

    private async Task<int> ReadWithTimeoutAsync(Stream src, byte[] buf, CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(_opt.NoDataTimeout);
        return await src.ReadAsync(buf, timeout.Token).ConfigureAwait(false);
    }

    private async Task ApplyRateLimitAsync(CancellationToken ct)
    {
        if (_opt.SpeedLimitBytesPerSecond <= 0) return;
        while (Volatile.Read(ref _rateLeft) <= 0)
        {
            await Task.Delay(16, ct).ConfigureAwait(false);
        }
    }

    // ---------------------------------------------------------------
    // 分段规划（对照 PCL：碎片不小于 256KB，且并发不超过最大线程数）
    // ---------------------------------------------------------------
    private void BuildPlan()
    {
        lock (_sync)
        {
            long size = _fileSize;
            if (size < _opt.MinSplitSize)
            {
                _chunks[0].End = size - 1;
                _nextChunkToStart = 1;
                return;
            }
            int total = (int)Math.Clamp((size + _opt.MinPieceSize - 1) / _opt.MinPieceSize, 1, _opt.MaxThreads);
            if (total == 1)
            {
                _chunks[0].End = size - 1;
                _nextChunkToStart = 1;
                return;
            }
            long baseLen = size / total;
            long rem = size % total;
            for (int i = 1; i < total; i++)
            {
                long start = ChunkStartFor(i, baseLen, rem);
                var c = new Chunk { Index = i, Start = start, End = i == total - 1 ? size - 1 : ChunkStartFor(i + 1, baseLen, rem) - 1, TempPath = TempPathFor(i) };
                _chunks.Add(c);
            }
            _chunks[0].End = ChunkStartFor(1, baseLen, rem) - 1;
            _nextChunkToStart = 1;
            Log($"文件大小 {_fileSize} 字节，计划分为 {total} 个分片并发下载。");
        }
    }

    private static long ChunkStartFor(int i, long baseLen, long rem) => i * baseLen + Math.Min(i, rem);

    private long ChunkEndFor(int index)
    {
        if (_chunks.Count == 1) return _fileSize - 1;
        long nextStart = index + 1 < _chunks.Count ? _chunks[index + 1].Start : _fileSize;
        return nextStart - 1;
    }

    // ---------------------------------------------------------------
    // 合并 / 降级 / 清理
    // ---------------------------------------------------------------
    private async Task<bool> TryMergeOrderedAsync(CancellationToken ct)
    {
        for (int attempt = 1; attempt <= 3; attempt++)
        {
            try
            {
                await using (var fs = new FileStream(_localPath, FileMode.Create, FileAccess.Write, FileShare.None, 81920, useAsync: true))
                {
                    foreach (var c in _chunks.OrderBy(c => c.Start))
                    {
                        if (!c.Finished || c.Done != c.End - c.Start + 1)
                            throw new IOException($"分片 {c.Index} 数据不完整（{c.Done}/{c.End - c.Start + 1}）。");
                        await using var part = new FileStream(c.TempPath, FileMode.Open, FileAccess.Read, FileShare.Read, 81920, useAsync: true);
                        await part.CopyToAsync(fs, 81920, ct).ConfigureAwait(false);
                    }
                    await fs.FlushAsync(ct).ConfigureAwait(false);
                }
                if (new FileInfo(_localPath).Length != _fileSize)
                    throw new IOException("合并后文件大小与预期不符。");
                Log("分片合并完成。");
                return true;
            }
            catch (OperationCanceledException) { throw; }
            catch (Exception ex)
            {
                Log($"合并失败（第 {attempt} 次）：{ex.Message}");
                try { File.Delete(_localPath); } catch { }
                if (attempt < 3) await Task.Delay(500 * attempt, ct).ConfigureAwait(false);
            }
        }
        return false;
    }

    private async Task FallbackSingleThreadAsync(CancellationToken ct)
    {
        await StopWorkersQuietlyAsync().ConfigureAwait(false);
        CleanTemps();
        Log("开始单线程整文件下载（服务器不支持分段或合并失败）。");
        Exception? last = null;
        for (int attempt = 0; attempt < 2; attempt++)
        {
            try
            {
                var tmp = Path.Combine(_cacheDir, $"{Path.GetFileName(_localPath)}.single.{Environment.ProcessId}.tmp");
                AddTemp(tmp);
                using var req = new HttpRequestMessage(HttpMethod.Get, _url);
                ApplyHeaders(req);
                using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false);
                if ((int)resp.StatusCode >= 400) throw new HttpRequestException($"HTTP {(int)resp.StatusCode}");
                await using var src = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
                long done = 0;
                {
                    // 注意：必须在关闭文件流之后再 Move，否则 Windows 下会因句柄占用失败
                    await using var dst = new FileStream(tmp, FileMode.Create, FileAccess.Write, FileShare.Read);
                    var buf = new byte[ReadBufferSize];
                    int n;
                    while ((n = await ReadWithTimeoutAsync(src, buf, ct).ConfigureAwait(false)) > 0)
                    {
                        // 降级路径不再限速：监控线程已退出，限速配额不再补充，避免死等
                        dst.Write(buf, 0, n);
                        done += n;
                        Interlocked.Add(ref _doneBytes, n);
                    }
                    await dst.FlushAsync(ct).ConfigureAwait(false);
                }
                if (_fileSize > 0 && done != _fileSize)
                    throw new IOException($"单线程下载大小不符：期望 {_fileSize}，实际 {done}。");
                File.Move(tmp, _localPath, overwrite: true);
                RemoveTemp(tmp);
                _fallbackFinished = true; // 结果标记：发生过降级
                Log("单线程下载完成。");
                return;
            }
            catch (OperationCanceledException) { throw; }
            catch (Exception ex)
            {
                last = ex;
                Log($"单线程下载失败（第 {attempt + 1} 次）：{ex.Message}");
                try { await Task.Delay(300, ct).ConfigureAwait(false); } catch (OperationCanceledException) { throw; }
            }
        }
        throw new Exception($"单线程下载失败：{last?.Message}");
    }

    private void MoveIntoPlace(string temp)
    {
        File.Move(temp, _localPath, overwrite: true);
        RemoveTemp(temp);
    }

    private async Task StopWorkersQuietlyAsync()
    {
        try { _chunkCts.Cancel(); } catch { }
        Task[] arr;
        lock (_sync) arr = _workers.ToArray();
        try { await Task.WhenAll(arr).ConfigureAwait(false); } catch { /* 工作线程内部已消化异常 */ }
    }

    private string TempPathFor(int index) =>
        Path.Combine(_cacheDir, $"{Path.GetFileName(_localPath)}.p{index}.{Environment.ProcessId}.tmp");

    private static readonly StringComparison PathComparison =
        Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;

    /// <summary>判断 child 是否位于 root 目录之内（含 root 本身，大小写按平台处理）。</summary>
    private static bool IsWithin(string root, string child)
    {
        var r = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var c = Path.GetFullPath(child).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return c.Equals(r, PathComparison) || c.StartsWith(r + Path.DirectorySeparatorChar, PathComparison);
    }

    private void AddTemp(string path)
    {
        lock (_sync) _temps.Add(path);
    }

    private void RemoveTemp(string path)
    {
        lock (_sync) _temps.Remove(path);
        try { File.Delete(path); } catch { }
    }

    private void CleanTempFile(string path)
    {
        try { if (!string.IsNullOrEmpty(path) && File.Exists(path)) File.Delete(path); } catch { }
    }

    private void CleanTemps()
    {
        List<string> paths;
        lock (_sync)
        {
            paths = _temps.ToList();
            _temps.Clear();
            foreach (var c in _chunks)
                if (!string.IsNullOrEmpty(c.TempPath))
                    paths.Add(c.TempPath);
        }
        foreach (var p in paths.Distinct()) CleanTempFile(p);
    }

    private void Log(string message)
    {
        try { LogMessage?.Invoke(this, message); }
        catch { /* 订阅方异常不应影响下载线程 */ }
    }

    private static long? ParseContentRangeTotal(ContentRangeHeaderValue? range)
    {
        if (range is not null && range.Length is long len && len > 0) return len;
        return null;
    }

    public void Dispose()
    {
        _lifetime.Cancel();
        _http.Dispose();
    }

    // ---------------- 内部类型 ----------------

    private sealed class Chunk
    {
        public int Index;
        public long Start;            // 闭区间起点
        public long End;              // 闭区间终点（未知大小时为 long.MaxValue）
        public long Done;
        public string TempPath = string.Empty;
        public volatile bool Finished;
        public volatile bool Faulted;
        public int RespawnCount;
        public long LastReceiveTime;
    }

    private sealed class RangeNotSupportedException : Exception
    {
        public RangeNotSupportedException(string message) : base(message) { }
    }

    private sealed class SlowConnectionException : Exception
    {
        public SlowConnectionException(string message) : base(message) { }
    }
}
using System.Net;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using CustomFileDownloader.Core;
using CustomFileDownloader.DownloaderHost;

// 自检：用本地 HTTP 服务器（支持/不支持 Range）验证下载引擎的
// 分段并行、Range 降级、小文件单线程、取消清理等行为，并校验文件哈希。
internal static class Program
{
    private static readonly Random Rnd = new(42);
    private static int _failed;

    private static async Task<int> Main()
    {
        Console.WriteLine("=== CustomFileDownloader 引擎自检 ===");
        Console.WriteLine();

        // ① 多线程分段下载（支持 Range）
        using (var server = new TestServer(4 * 1024 * 1024, supportRange: true))
            await RunDownloadTestAsync("① 多线程分段下载（4MB，支持 Range）", server,
                expectMultiThread: true, expectFallback: false);

        // ② 服务器不支持 Range → 自动降级单线程
        using (var server = new TestServer(4 * 1024 * 1024, supportRange: false))
            await RunDownloadTestAsync("② 不支持 Range 自动降级（4MB）", server,
                expectMultiThread: false, expectFallback: true);

        // ③ 小文件不分段（512KB < 1MB）
        using (var server = new TestServer(512 * 1024, supportRange: true))
            await RunDownloadTestAsync("③ 小文件单线程（512KB）", server,
                expectMultiThread: false, expectFallback: false);

        // ④ 用户取消后无残留
        await RunCancelTestAsync();

        // ⑤ 大文件充分铺满并发（32MB，支持 Range）
        using (var server = new TestServer(32 * 1024 * 1024, supportRange: true))
            await RunDownloadTestAsync("⑤ 大文件并发（32MB，支持 Range）", server,
                expectMultiThread: true, expectFallback: false);

        // ⑥ 成品保存到根目录之外（保存位置不限的新要求）
        using (var server = new TestServer(2 * 1024 * 1024, supportRange: true))
            await RunOutsideRootTestAsync("⑥ 成品可保存到根目录之外", server);

        // ⑦ 极速模式：忽略速度阈值、直接铺满并发（8MB / 256KB 分片 = 32 个分片）
        using (var server = new TestServer(8 * 1024 * 1024, supportRange: true))
            await RunTurboTestAsync("⑦ 极速模式铺满线程", server);

        // ⑧ 浏览器接管契约：JSON 往返 / 帧格式
        RunContractTests();

        // ⑨ 自定义请求头（浏览器会话 Cookie）随分片请求送达服务器
        using (var server = new TestServer(2 * 1024 * 1024, supportRange: true))
            await RunHeaderTestAsync("⑨ 浏览器会话 Cookie 头随请求送达", server);

        Console.WriteLine();
        if (_failed == 0)
        {
            Console.WriteLine("✔ 全部通过，引擎自检成功（哈希一致、无残留临时文件）。");
            return 0;
        }
        Console.WriteLine($"✘ 共 {_failed} 项失败。");
        return 1;
    }

    private static async Task RunDownloadTestAsync(string name, TestServer server, bool expectMultiThread, bool expectFallback)
    {
        var dir = CreateTestDir();
        try
        {
            var path = Path.Combine(dir, "cache", "out.bin");
            using var dl = new SegmentedDownloader(new DownloadOptions
            {
                RootDirectory = dir,
                CacheDirectory = Path.Combine(dir, "cache"),
            });
            var result = await dl.DownloadAsync(server.BaseUrl + "f.bin", path);

            var hashOk = Sha256(path) == Convert.ToHexString(SHA256.HashData(server.Data));
            var multiOk = result.UsedMultiThread == expectMultiThread;
            var fallbackOk = result.UsedRangeFallback == expectFallback;
            var cleanOk = Directory.GetFiles(dir, "*.tmp", SearchOption.AllDirectories).Length == 0;

            Pass(name, hashOk && multiOk && fallbackOk && cleanOk,
                $"hash={hashOk} multi({result.UsedMultiThread}) fallback({result.UsedRangeFallback}) 清理={cleanOk} " +
                $"最大并发={result.MaxConcurrentThreads} 分片={result.TotalSegments} " +
                $"峰值={FormatBytes((long)result.PeakSpeedBytesPerSecond)}/s 用时={result.ElapsedSeconds:F2}s");
        }
        catch (Exception ex)
        {
            Fail(name, $"异常：{ex.Message}");
        }
        finally
        {
            TryDeleteDir(dir);
        }
    }

    private static async Task RunCancelTestAsync()
    {
        using var server = new TestServer(32 * 1024 * 1024, supportRange: true);
        var dir = CreateTestDir();
        try
        {
            var path = Path.Combine(dir, "cache", "cancel.bin");
            using var cts = new CancellationTokenSource(300);
            using var dl = new SegmentedDownloader(new DownloadOptions
            {
                MaxThreads = 1,
                SpeedLimitBytesPerSecond = 128 * 1024, // 放慢以便取消来得及
                RootDirectory = dir,
                CacheDirectory = Path.Combine(dir, "cache"),
            });
            try
            {
                await dl.DownloadAsync(server.BaseUrl + "f.bin", path, cts.Token);
                Fail("④ 取消下载", "预期抛出 OperationCanceledException，但下载竟然完成了。");
            }
            catch (OperationCanceledException)
            {
                var noFinal = !File.Exists(path);
                var noTemps = Directory.GetFiles(dir, "*.tmp", SearchOption.AllDirectories).Length == 0;
                Pass("④ 用户取消并清理", noFinal && noTemps,
                    $"成品残留={!noFinal} 临时文件残留={!noTemps}");
            }
            catch (Exception ex)
            {
                Fail("④ 取消下载", $"出现其它异常：{ex.Message}");
            }
        }
        finally
        {
            TryDeleteDir(dir);
        }
    }

    private static async Task RunOutsideRootTestAsync(string name, TestServer server)
    {
        var rootDir = CreateTestDir();
        var outsideDir = CreateTestDir();
        try
        {
            // 成品故意放在根目录之外（允许）；中间产物仍在根内 cache
            var path = Path.Combine(outsideDir, "out.bin");
            using var dl = new SegmentedDownloader(new DownloadOptions
            {
                RootDirectory = rootDir,
                CacheDirectory = Path.Combine(rootDir, "cache"),
            });
            var result = await dl.DownloadAsync(server.BaseUrl + "f.bin", path);

            var saved = File.Exists(path);
            var hashOk = saved && Sha256(path) == Convert.ToHexString(SHA256.HashData(server.Data));
            var cacheClean = Directory.GetFiles(rootDir, "*.tmp", SearchOption.AllDirectories).Length == 0;
            Pass(name, saved && hashOk && cacheClean,
                $"根外成品={saved} 哈希一致={hashOk} 根内缓存无残留={cacheClean} 并发={result.MaxConcurrentThreads}");
        }
        catch (Exception ex)
        {
            Fail(name, $"异常：{ex.Message}");
        }
        finally
        {
            TryDeleteDir(rootDir);
            TryDeleteDir(outsideDir);
        }
    }

    private static async Task RunTurboTestAsync(string name, TestServer server)
    {
        var dir = CreateTestDir();
        try
        {
            var path = Path.Combine(dir, "cache", "turbo.bin");
            using var dl = new SegmentedDownloader(new DownloadOptions
            {
                RootDirectory = dir,
                CacheDirectory = Path.Combine(dir, "cache"),
                MaxThreads = 64,
                AlwaysFillThreads = true,
            });
            var result = await dl.DownloadAsync(server.BaseUrl + "f.bin", path);
            var hashOk = Sha256(path) == Convert.ToHexString(SHA256.HashData(server.Data));
            var cleanOk = Directory.GetFiles(dir, "*.tmp", SearchOption.AllDirectories).Length == 0;
            var turboOk = result.MaxConcurrentThreads >= 8;
            Pass(name, hashOk && cleanOk && turboOk,
                $"最大并发={result.MaxConcurrentThreads}（期待 ≥8） 哈希一致={hashOk} 缓存清理={cleanOk}");
        }
        catch (Exception ex)
        {
            Fail(name, $"异常：{ex.Message}");
        }
        finally
        {
            TryDeleteDir(dir);
        }
    }

    // ---------------- 浏览器接管契约与请求头 ---------------- //

    private static void RunContractTests()
    {
        var req = new TakeoverRequest
        {
            Type = "download",
            Url = "https://download.nexusmods.com/files/x.7z?fid=1&key=abc",
            Filename = "x.7z",
            Cookie = "sid=abc; key=def",
            Referrer = "https://www.nexusmods.com/skyrimspecialedition/mods/1",
        };
        var json = NativeJson.Serialize(req);
        var back = NativeJson.Deserialize<TakeoverRequest>(json);
        Pass("⑧ 接管请求 JSON 往返（camelCase）",
            back is not null && back.Url == req.Url && back.Filename == req.Filename &&
            back.Cookie == req.Cookie && back.Referrer == req.Referrer,
            json);

        // 扩展实际发来的 camelCase JSON 应能解析
        var fromExt = NativeJson.Deserialize<TakeoverRequest>(
            "{\"type\":\"download\",\"url\":\"https://download.nexusmods.com/f.bin\",\"cookie\":\"sid=1\"}");
        Pass("⑧b 接管消息（camelCase）解析",
            fromExt is { Url: "https://download.nexusmods.com/f.bin", Cookie: "sid=1" },
            $"Type={fromExt?.Type}");

        // 宿主回执 JSON 往返
        var resp = new TakeoverResponse { Ok = true, Message = "enqueued", JobId = "T001" };
        var respJson = NativeJson.Serialize(resp);
        var respBack = NativeJson.Deserialize<TakeoverResponse>(respJson);
        Pass("⑧c 回执 JSON 往返", respBack is { Ok: true, JobId: "T001" }, respJson);
    }

    private static async Task RunHeaderTestAsync(string name, TestServer server)
    {
        var dir = CreateTestDir();
        try
        {
            server.LastCookie = null;
            var path = Path.Combine(dir, "cache", "hdr.bin");
            using var dl = new SegmentedDownloader(new DownloadOptions
            {
                RootDirectory = dir,
                CacheDirectory = Path.Combine(dir, "cache"),
                MaxThreads = 4,
                Headers = new Dictionary<string, string>
                {
                    ["Cookie"] = "sid=abc123",
                    ["Referer"] = "https://www.nexusmods.com/",
                },
            });
            var result = await dl.DownloadAsync(server.BaseUrl + "f.bin", path);
            var hashOk = Sha256(path) == Convert.ToHexString(SHA256.HashData(server.Data));
            var cookieOk = server.LastCookie == "sid=abc123";
            Pass(name, hashOk && cookieOk && result.UsedMultiThread,
                $"哈希一致={hashOk} 服务器收到 Cookie={cookieOk} 多线程={result.UsedMultiThread} 并发={result.MaxConcurrentThreads}");
        }
        catch (Exception ex)
        {
            Fail(name, $"异常：{ex.Message}");
        }
        finally
        {
            TryDeleteDir(dir);
        }
    }

    // ---------------- 工具 ---------------- //

    private static string CreateTestDir()
    {
        var dir = Path.Combine(Path.GetTempPath(), "pcl-dl-test-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        return dir;
    }

    private static void TryDeleteDir(string dir)
    {
        try { Directory.Delete(dir, recursive: true); } catch { }
    }

    private static string Sha256(string path)
    {
        using var fs = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(fs));
    }

    private static void Pass(string name, bool ok, string detail)
    {
        Console.WriteLine($"{(ok ? "[通过]" : "[失败]")} {name}");
        Console.WriteLine($"        {detail}");
        if (!ok) _failed++;
    }

    private static void Fail(string name, string detail)
    {
        Console.WriteLine($"[失败] {name}");
        Console.WriteLine($"        {detail}");
        _failed++;
    }

    private static string FormatBytes(long bytes) => bytes switch
    {
        >= 1024L * 1024 * 1024 => $"{bytes / 1024.0 / 1024 / 1024:F2} GB",
        >= 1024L * 1024 => $"{bytes / 1024.0 / 1024:F1} MB",
        >= 1024L => $"{bytes / 1024.0:F1} KB",
        _ => $"{bytes} B",
    };

    // ---------------- 本地测试服务器 ---------------- //

    private sealed class TestServer : IDisposable
    {
        private readonly HttpListener _listener = new();
        private readonly Thread _thread;
        private readonly bool _supportRange;
        private volatile bool _running = true;

        public byte[] Data { get; }
        public string BaseUrl { get; }
        /// <summary>最近一次请求携带的 Cookie 头（用于验证自定义请求头送达）。</summary>
        public volatile string? LastCookie;

        public TestServer(long byteCount, bool supportRange)
        {
            _supportRange = supportRange;
            Data = new byte[byteCount];
            Rnd.NextBytes(Data);
            int port = FindFreePort();
            BaseUrl = $"http://localhost:{port}/";
            _listener.Prefixes.Add(BaseUrl);
            _listener.Start();
            _thread = new Thread(Loop) { IsBackground = true, Name = "test-server" };
            _thread.Start();
        }

        private void Loop()
        {
            while (_running && _listener.IsListening)
            {
                HttpListenerContext ctx;
                try { ctx = _listener.GetContext(); }
                catch { return; }
                try { Handle(ctx); }
                catch { try { ctx.Response.Abort(); } catch { } }
            }
        }

        private void Handle(HttpListenerContext ctx)
        {
            var resp = ctx.Response;
            LastCookie = ctx.Request.Headers["Cookie"];
            string? range = ctx.Request.Headers["Range"];
            long start = 0, end = Data.Length - 1;
            bool partial = false;
            if (!string.IsNullOrEmpty(range) && _supportRange)
            {
                var m = Regex.Match(range, @"bytes=(\d+)-(\d*)");
                if (m.Success)
                {
                    start = long.Parse(m.Groups[1].Value);
                    if (m.Groups[2].Value.Length > 0) end = Math.Min(end, long.Parse(m.Groups[2].Value));
                    partial = true;
                }
            }
            long len = end - start + 1;
            resp.StatusCode = partial ? (int)HttpStatusCode.PartialContent : (int)HttpStatusCode.OK;
            if (partial) resp.AddHeader("Content-Range", $"bytes {start}-{end}/{Data.Length}");
            resp.ContentLength64 = len;
            resp.ContentType = "application/octet-stream";
            using var body = resp.OutputStream;
            body.Write(Data, checked((int)start), checked((int)len));
        }

        private static int FindFreePort()
        {
            using var l = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
            l.Start();
            int port = ((IPEndPoint)l.LocalEndpoint).Port;
            l.Stop();
            return port;
        }

        public void Dispose()
        {
            _running = false;
            try { _listener.Stop(); } catch { }
            try { _listener.Close(); } catch { }
        }
    }
}
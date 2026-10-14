using System.Diagnostics;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace NexusApp;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Environment.SetEnvironmentVariable("WEBVIEW2_DEFAULT_BACKGROUND_COLOR", "FF111111");
        Application.Run(new MainForm());
    }
}

/// <summary>主窗口：独立桌面界面（WebView2 加载本地仪表盘）。</summary>
internal sealed class MainForm : Form
{
    private readonly WebView2 _web = new();
    private readonly StartupScreen _startup = new();
    private readonly CancellationTokenSource _closing = new();
    private static readonly HttpClient HealthClient = new(new HttpClientHandler { UseProxy = false })
        { Timeout = TimeSpan.FromSeconds(2) };
    private readonly string _baseUrl;
    private Process? _backend;
    private TaskCompletionSource? _pageReady;
    private bool _starting;
    private bool _startupFinished;

    public MainForm(string? baseUrl = null)
    {
        _baseUrl = baseUrl ?? "http://127.0.0.1:8000";
        Text = "Nexus History Downloader";
        Icon = Icon.ExtractAssociatedIcon(Environment.ProcessPath ?? "NHD.exe");
        Size = new Size(1280, 820);
        StartPosition = FormStartPosition.CenterScreen;
        WindowState = FormWindowState.Maximized;
        BackColor = Color.FromArgb(17, 17, 17);
        ForeColor = Color.FromArgb(237, 237, 237);
        _web.Dock = DockStyle.Fill;
        _web.DefaultBackgroundColor = BackColor;
        _startup.Dock = DockStyle.Fill;
        Controls.Add(_web);
        Controls.Add(_startup);
        _startup.BringToFront();
        _startup.RetryRequested += async (_, _) => await StartAsync();
        FormClosing += (_, _) => { _closing.Cancel(); StopBackend(); };
        Shown += async (_, _) => await StartAsync();
    }

    private async Task StartAsync()
    {
        if (_starting || _closing.IsCancellationRequested) return;
        _starting = true;
        _startupFinished = false;
        _startup.Visible = true;
        _startup.BringToFront();
        _startup.SetProgress(2, "正在检查启动环境");
        var cancellationToken = _closing.Token;
        try
        {
            if (!await IsUp(_baseUrl, cancellationToken))
            {
                _startup.SetProgress(5, "正在准备程序资源");
                var progress = new Progress<int>(value =>
                {
                    if (!_closing.IsCancellationRequested && !IsDisposed)
                        _startup.SetProgress(5 + value * 27 / 100, "正在准备程序资源");
                });
                var exe = await Task.Run(() => Payload.ExtractBackend(progress, cancellationToken), cancellationToken);
                cancellationToken.ThrowIfCancellationRequested();
                if (exe is null) throw new InvalidOperationException("缺少内置后端组件，请重新安装 NHD。");
                _startup.SetProgress(35, "正在启动本地服务");
                if (_backend is not null) { StopBackend(); _backend.Dispose(); }
                var psi = new ProcessStartInfo(exe)
                {
                    WorkingDirectory = AppContext.BaseDirectory,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                };
                psi.Environment["NEXUS_NO_AUTOBROWSER"] = "1";
                psi.Environment["NHD_APP_DIR"] = AppContext.BaseDirectory;
                _backend = Process.Start(psi) ?? throw new InvalidOperationException("本地服务未能启动。");
                var ready = false;
                for (var i = 0; i < 60; i++)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    if (_backend.HasExited) throw new InvalidOperationException("本地服务意外退出，请查看 cache\\nexus-dashboard\\app.log。");
                    if (await IsUp(_baseUrl, cancellationToken)) { ready = true; break; }
                    _startup.SetProgress(38, $"正在连接本地服务 · {i + 1} 秒");
                    await Task.Delay(1000, cancellationToken);
                }
                if (!ready) throw new TimeoutException("本地服务启动超时，请查看 cache\\nexus-dashboard\\app.log。");
            }

            cancellationToken.ThrowIfCancellationRequested();
            _startup.SetProgress(58, "正在初始化界面");
            if (_web.CoreWebView2 is null)
            {
                var env = await CoreWebView2Environment.CreateAsync(
                    null, Path.Combine(AppContext.BaseDirectory, "webview"));
                cancellationToken.ThrowIfCancellationRequested();
                await _web.EnsureCoreWebView2Async(env);
                cancellationToken.ThrowIfCancellationRequested();
                var core = _web.CoreWebView2 ?? throw new InvalidOperationException("界面组件未能初始化，请重试。");
                core.Profile.PreferredColorScheme = CoreWebView2PreferredColorScheme.Dark;
                core.WebMessageReceived += OnStartupMessage;
                core.NavigationCompleted += (_, e) =>
                {
                    if (!e.IsSuccess || e.HttpStatusCode >= 400)
                        _pageReady?.TrySetException(new InvalidOperationException("界面加载失败，请重试。"));
                };
                core.ProcessFailed += (_, e) =>
                {
                    if (e.ProcessFailedKind is CoreWebView2ProcessFailedKind.BrowserProcessExited
                        or CoreWebView2ProcessFailedKind.RenderProcessExited
                        or CoreWebView2ProcessFailedKind.RenderProcessUnresponsive)
                        _pageReady?.TrySetException(new InvalidOperationException("界面进程中断，请重新打开 NHD。"));
                };
            }
            _pageReady = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            _startup.BringToFront();
            _startup.SetProgress(68, "正在加载工作台");
            _web.CoreWebView2.Navigate(_baseUrl);
            await _pageReady.Task.WaitAsync(TimeSpan.FromSeconds(45), cancellationToken);
            cancellationToken.ThrowIfCancellationRequested();
            await _startup.FinishAsync(cancellationToken);
            _startupFinished = true;
            _startup.Visible = false;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { }
        catch (Exception ex)
        {
            if (!_closing.IsCancellationRequested && !IsDisposed)
            {
                _startup.ShowError(ex is TimeoutException ? "启动等待超时，请重试；若仍失败，请重新打开 NHD。" : ex.Message);
                _startup.BringToFront();
            }
        }
        finally { _pageReady = null; _starting = false; }
    }

    private void OnStartupMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (_startupFinished || _closing.IsCancellationRequested || _pageReady is null) return;
        if (!Uri.TryCreate(e.Source, UriKind.Absolute, out var source) ||
            source.GetLeftPart(UriPartial.Authority) != _baseUrl) return;
        try
        {
            using var json = JsonDocument.Parse(e.WebMessageAsJson);
            var root = json.RootElement;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("type", out var type) ||
                type.GetString() != "nhd-startup" || !root.TryGetProperty("stage", out var stage)) return;
            switch (stage.GetString())
            {
                case "page-started": _startup.SetProgress(72, "正在连接工作台"); break;
                case "settings-ready": _startup.SetProgress(82, "正在读取模组列表"); break;
                case "library-ready": _startup.SetProgress(94, "正在完成界面绘制"); break;
                case "ready": _pageReady.TrySetResult(); break;
                case "error":
                    _pageReady.TrySetException(new InvalidOperationException("工作台初始化失败，请重试。"));
                    break;
            }
        }
        catch (JsonException) { /* 忽略与启动无关的网页消息。 */ }
        catch (InvalidOperationException) { }
    }

    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        ApplyDarkTitleBar();
    }

    protected override void WndProc(ref Message m)
    {
        base.WndProc(ref m);
        if (m.Msg is 0x031A or 0x001A) ApplyDarkTitleBar();
    }

    private void ApplyDarkTitleBar()
    {
        if (!IsHandleCreated) return;
        var dark = 1;
        if (DwmSetWindowAttribute(Handle, 20, ref dark, sizeof(int)) != 0)
            DwmSetWindowAttribute(Handle, 19, ref dark, sizeof(int));
        var caption = 0x171717;
        var text = 0xEDEDED;
        DwmSetWindowAttribute(Handle, 35, ref caption, sizeof(int));
        DwmSetWindowAttribute(Handle, 36, ref text, sizeof(int));
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int size);

    private static async Task<bool> IsUp(string url, CancellationToken cancellationToken)
    {
        try
        {
            using var response = await HealthClient.GetAsync(url + "/api/status", cancellationToken);
            return response.IsSuccessStatusCode;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { throw; }
        catch (HttpRequestException) { return false; }
        catch (TaskCanceledException) { return false; }
    }

    private void StopBackend()
    {
        if (_backend is null) return;
        try
        {
            if (_backend.HasExited) return;
            _backend.Kill(true);
            _backend.WaitForExit(3000);
        }
        catch (InvalidOperationException) { /* 已退出。 */ }
        catch (System.ComponentModel.Win32Exception) { /* 已退出。 */ }
    }
}

/// <summary>内嵌后端提取：exe 尾部 = [backend payload][长度 8][magic]（构建脚本拼接）。</summary>
internal static class Payload
{
    private const string Magic = "NEXUSPAYLOAD";
    private const string BackendName = "nexus-backend.exe";

    public static string? ExtractBackend(IProgress<int>? progress = null, CancellationToken cancellationToken = default)
    {
        string? temporary = null;
        try
        {
            var self = Path.GetFullPath(Environment.ProcessPath ?? AppContext.BaseDirectory);
            if (!File.Exists(self)) self = Path.Combine(AppContext.BaseDirectory, "NHD.exe");
            if (!File.Exists(self)) return null;
            var trailer = Encoding.ASCII.GetBytes(Magic);
            var len = 8 + trailer.Length;
            using var input = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (input.Length < len + 16) return null;
            input.Seek(-len, SeekOrigin.End);
            var tail = new byte[len];
            input.ReadExactly(tail);
            if (!tail.AsSpan(8).SequenceEqual(trailer)) return null;
            var payloadLen = BitConverter.ToInt64(tail, 0);
            var payloadStart = input.Length - len - payloadLen;
            if (payloadLen <= 0 || payloadStart < 0) return null;
            var root = Path.Combine(AppContext.BaseDirectory, ".runtime");
            Directory.CreateDirectory(root);
            var target = Path.Combine(root, BackendName);
            var mtime = File.GetLastWriteTimeUtc(self);
            if (!File.Exists(target) || new FileInfo(target).Length != payloadLen || File.GetLastWriteTimeUtc(target) < mtime)
            {
                temporary = target + $".{Environment.ProcessId}.tmp";
                input.Position = payloadStart;
                using (var output = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None))
                {
                    var buffer = new byte[1024 * 1024];
                    long copied = 0;
                    while (copied < payloadLen)
                    {
                        cancellationToken.ThrowIfCancellationRequested();
                        var count = (int)Math.Min(buffer.Length, payloadLen - copied);
                        input.ReadExactly(buffer.AsSpan(0, count));
                        output.Write(buffer, 0, count);
                        copied += count;
                        progress?.Report((int)(copied * 100 / payloadLen));
                    }
                }
                cancellationToken.ThrowIfCancellationRequested();
                File.Move(temporary, target, true);
                temporary = null;
            }
            progress?.Report(100);
            return target;
        }
        catch (OperationCanceledException) { throw; }
        catch (IOException) { return null; }
        catch (UnauthorizedAccessException) { return null; }
        finally
        {
            if (temporary is not null)
            {
                try { File.Delete(temporary); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            }
        }
    }
}

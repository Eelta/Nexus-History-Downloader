using System.Diagnostics;
using System.Net.Http;
using System.Text;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace NexusApp;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new MainForm());
    }
}

/// <summary>主窗口：独立桌面界面（WebView2 加载本地仪表盘）。</summary>
internal sealed class MainForm : Form
{
    private readonly WebView2 _web = new();
    private Process? _backend;
    private const string BaseUrl = "http://127.0.0.1:8000";

    public MainForm()
    {
        Text = "Nexus History Downloader";
        Icon = Icon.ExtractAssociatedIcon(Environment.ProcessPath ?? "NHD.exe");
        Size = new Size(1280, 820);
        StartPosition = FormStartPosition.CenterScreen;
        _web.Dock = DockStyle.Fill;
        Controls.Add(_web);
        FormClosing += (_, _) => StopBackend();
        Shown += async (_, _) => await StartAsync();
    }

    private async Task StartAsync()
    {
        if (!await IsUp(BaseUrl))
        {
            var exe = Payload.ExtractBackend();
            if (exe is null)
            {
                MessageBox.Show("缺少内置后端组件。", "启动失败");
                Close();
                return;
            }
            var psi = new ProcessStartInfo(exe)
            {
                WorkingDirectory = AppContext.BaseDirectory,   // 数据在 exe 旁（安装文件夹）
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            psi.Environment["NEXUS_NO_AUTOBROWSER"] = "1";     // 由本窗口承载界面
            psi.Environment["NHD_APP_DIR"] = AppContext.BaseDirectory;  // 数据目录 = 应用文件夹
            _backend = Process.Start(psi);
            for (var i = 0; i < 60 && !await IsUp(BaseUrl); i++)
            {
                await Task.Delay(1000);
            }
        }
        if (!await IsUp(BaseUrl))
        {
            MessageBox.Show("后端启动失败，请查看应用目录下 cache\\nexus-dashboard\\app.log。", "启动失败");
            StopBackend();
            Close();
            return;
        }
        try
        {
            var env = await CoreWebView2Environment.CreateAsync(
                null, Path.Combine(AppContext.BaseDirectory, "webview"));
            await _web.EnsureCoreWebView2Async(env);
            _web.CoreWebView2.Navigate(BaseUrl);
        }
        catch (Exception ex)
        {
            MessageBox.Show("界面初始化失败：" + ex.Message, "启动失败");
            StopBackend();
            Close();
        }
    }

    private static async Task<bool> IsUp(string url)
    {
        try
        {
            using var c = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
            return (await c.GetAsync(url + "/api/status")).IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private void StopBackend()
    {
        if (_backend is null || _backend.HasExited) return;
        try
        {
            _backend.Kill(true);
            _backend.WaitForExit(3000);
        }
        catch { /* 已退出 */ }
    }
}

/// <summary>内嵌后端提取：exe 尾部 = [backend payload][长度 8][magic]（构建脚本拼接）。</summary>
internal static class Payload
{
    private const string Magic = "NEXUSPAYLOAD";
    private const string BackendName = "nexus-backend.exe";

    public static string? ExtractBackend()
    {
        try
        {
            var self = Path.GetFullPath(Environment.ProcessPath ?? AppContext.BaseDirectory);
            if (!File.Exists(self)) self = Path.Combine(AppContext.BaseDirectory, "NHD.exe");
            if (!File.Exists(self)) return null;
            var bytes = File.ReadAllBytes(self);
            var trailer = Encoding.ASCII.GetBytes(Magic);
            var len = 8 + trailer.Length;
            if (bytes.Length < len + 16) return null;
            var magicStart = bytes.Length - trailer.Length;
            for (var i = magicStart - 8; i <= magicStart; i++)
            {
                var ok = true;
                for (var j = 0; j < trailer.Length; j++)
                {
                    if (bytes[i + j] != trailer[j]) { ok = false; break; }
                }
                if (!ok) continue;
                var payloadLen = BitConverter.ToInt64(bytes, i - 8);
                if (payloadLen <= 0 || i - 8 - payloadLen < 0) return null;
                var root = Path.Combine(AppContext.BaseDirectory, ".runtime");
                Directory.CreateDirectory(root);
                var target = Path.Combine(root, BackendName);
                var mtime = new FileInfo(self).LastWriteTimeUtc;
                if (!File.Exists(target) || new FileInfo(target).LastWriteTimeUtc < mtime)
                {
                    using var fs = new FileStream(target, FileMode.Create, FileAccess.Write);
                    fs.Write(bytes, (int)(i - 8 - payloadLen), (int)payloadLen);
                }
                return target;
            }
        }
        catch { /* 提取失败由调用方提示 */ }
        return null;
    }
}
using System.Diagnostics;
using System.Text;
using System.Windows.Forms;

namespace NexusSetup;

internal static class Program
{
    private const string Magic = "NEXUSSETUPPAYLOAD";
    private const string AppName = "NHD";
    private const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\NHD";

    [STAThread]
    private static int Main(string[] args)
    {
        int installArg = Array.IndexOf(args, "--install");
        string? instDir = installArg >= 0 && installArg + 1 < args.Length ? args[installArg + 1] : null;
        int uninstArg = Array.IndexOf(args, "--uninstall");
        string? dirArg = uninstArg >= 0 && uninstArg + 1 < args.Length ? args[uninstArg + 1] : null;

        ApplicationConfiguration.Initialize();
        if (instDir is not null)                              // 静默安装
        {
            return InstallSilent(instDir, Array.IndexOf(args, "--shortcut") >= 0);
        }
        if (dirArg is not null)                               // 控制面板 / 静默卸载
        {
            return Uninstall(Path.GetFullPath(dirArg).TrimEnd('\\', '/'));
        }
        if (Path.GetFileName(Environment.ProcessPath ?? "").Equals("Uninstall.exe", StringComparison.OrdinalIgnoreCase))
        {
            // 双击 Uninstall.exe：自动定位安装位置，确认后卸载
            string dir = LocateInstallDir();
            if (MessageBox.Show(
                    "确定要卸载 Nexus History Downloader 吗？\n卸载将删除安装文件夹中的全部内容（含下载文件与登录会话）。",
                    "卸载", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes)
            {
                return Uninstall(dir);
            }
            return 0;
        }
        Application.Run(new SetupWizard());
        return 0;
    }

    private static string LocateInstallDir()
    {
        string dir = AppContext.BaseDirectory;
        try
        {
            using var k = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(UninstallKey);
            var loc = k?.GetValue("InstallLocation") as string;
            if (!string.IsNullOrWhiteSpace(loc)) dir = loc;
        }
        catch { }
        return Path.GetFullPath(dir).TrimEnd('\\', '/');
    }

    private static int InstallSilent(string dir, bool shortcut)
    {
        string? err = Install(NormalizeDir(dir), shortcut, out _);
        Console.WriteLine(err is null ? "INSTALL_OK" : "INSTALL_FAIL " + err);
        return err is null ? 0 : 1;
    }

    internal static string? Install(string dir, bool shortcut, out string target)
    {
        target = Path.Combine(dir, AppName + ".exe");
        try
        {
            ExtractApp(target);
            File.Copy(Self(), Path.Combine(dir, "Uninstall.exe"), true);
            if (shortcut) CreateShortcut(target, dir);
            using var k = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(UninstallKey);
            k.SetValue("DisplayName", "Nexus History Downloader");
            k.SetValue("DisplayVersion", "2.35");
            k.SetValue("InstallLocation", dir);
            k.SetValue("DisplayIcon", target);
            k.SetValue("UninstallString", $"\"{Path.Combine(dir, "Uninstall.exe")}\" --uninstall \"{dir}\"");
            k.SetValue("NoModify", 1);
            k.SetValue("NoRepair", 1);
            return null;
        }
        catch (Exception ex)
        {
            return ex.Message;
        }
    }

    private static int Uninstall(string dir)
    {
        try
        {
            var lnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                                   AppName + ".lnk");
            if (File.Exists(lnk)) File.Delete(lnk);
            Microsoft.Win32.Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
            // 延迟删除安装目录（本进程退出后由 cmd 完成）
            Process.Start(new ProcessStartInfo(
                "cmd.exe", $"/c ping -n 2 127.0.0.1 >nul & rmdir /s /q \"{dir}\"")
            { UseShellExecute = false, CreateNoWindow = true });
            return 0;
        }
        catch (Exception ex)
        {
            Console.WriteLine("UNINSTALL_FAIL " + ex.Message);
            return 1;
        }
    }

    /// <summary>规范化安装位置：末段是 NHD 则用之，否则自动补 \NHD。</summary>
    internal static string NormalizeDir(string input)
    {
        var d = (input ?? "").Trim().TrimEnd('\\', '/');
        if (d.Length == 0) return d;
        return string.Equals(Path.GetFileName(d), "NHD", StringComparison.OrdinalIgnoreCase)
            ? d
            : Path.Combine(d, "NHD");
    }

    private static string Self()
    {
        var p = Path.GetFullPath(Environment.ProcessPath ?? AppContext.BaseDirectory);
        return File.Exists(p) ? p : Path.Combine(AppContext.BaseDirectory, "NHD.exe");
    }

    /// <summary>从自身尾部提取内嵌应用（构建脚本拼接：[app][长度 8][magic]）。</summary>
    private static void ExtractApp(string target)
    {
        var bytes = File.ReadAllBytes(Self());
        var magic = Encoding.ASCII.GetBytes(Magic);
        var tail = 8 + magic.Length;
        var magicStart = bytes.Length - magic.Length;
        for (var i = magicStart - 8; i <= magicStart; i++)
        {
            var ok = true;
            for (var j = 0; j < magic.Length; j++)
            {
                if (bytes[i + j] != magic[j]) { ok = false; break; }
            }
            if (!ok) continue;
            var len = BitConverter.ToInt64(bytes, i - 8);
            if (len <= 0 || i - 8 - len < 0) throw new InvalidDataException("payload 损坏");
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            using var fs = new FileStream(target, FileMode.Create, FileAccess.Write);
            fs.Write(bytes, (int)(i - 8 - len), (int)len);
            return;
        }
        throw new InvalidDataException("未找到内嵌应用");
    }

    private static void CreateShortcut(string target, string dir)
    {
        var desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell")!)!;
        dynamic lnk = shell.CreateShortcut(Path.Combine(desktop, AppName + ".lnk"));
        lnk.TargetPath = target;
        lnk.WorkingDirectory = dir;
        lnk.Description = "Nexus History Downloader";
        lnk.Save();
    }
}

/// <summary>标准安装向导：欢迎 → 安装位置 → 选项 → 完成。</summary>
internal sealed class SetupWizard : Form
{
    private readonly TextBox _dir = new() { Width = 320 };
    private readonly CheckBox _shortcut = new() { Text = "创建桌面快捷方式", Checked = true };
    private readonly Label _title = new() { AutoSize = true, Font = new Font("Microsoft YaHei UI", 13, FontStyle.Bold) };
    private readonly Label _subtitle = new() { AutoSize = true, ForeColor = Color.Gray };
    private readonly Panel _body = new() { Location = new Point(12, 74), Size = new Size(616, 290) };
    private readonly Button _back = new() { Text = "上一步", Size = new Size(110, 30) };
    private readonly Button _next = new() { Text = "下一步", Size = new Size(110, 30) };
    private readonly Button _cancel = new() { Text = "取消", Size = new Size(110, 30) };
    private readonly Label _result = new() { AutoSize = true, MaximumSize = new Size(560, 0) };
    private int _step;

    public SetupWizard()
    {
        Text = "Nexus History Downloader 安装程序";
        ClientSize = new Size(640, 420);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;

        var icon = new PictureBox
        {
            Size = new Size(48, 48),
            Location = new Point(14, 12),
            SizeMode = PictureBoxSizeMode.Zoom,
        };
        try { icon.Image = Icon.ExtractAssociatedIcon(Environment.ProcessPath ?? "NHD.exe").ToBitmap(); } catch { }
        _title.Location = new Point(76, 18);
        _subtitle.Location = new Point(76, 50);

        var footer = new Panel { Dock = DockStyle.Bottom, Height = 60 };
        footer.Controls.Add(new Label { Dock = DockStyle.Top, Height = 1, BackColor = Color.Gainsboro });
        _back.Location = new Point(640 - 3 * 118 - 8, 16);
        _next.Location = new Point(640 - 2 * 118 - 8, 16);
        _cancel.Location = new Point(640 - 118 - 8, 16);
        footer.Controls.Add(_cancel);
        footer.Controls.Add(_next);
        footer.Controls.Add(_back);

        Controls.Add(footer);
        Controls.Add(_body);
        Controls.Add(_subtitle);
        Controls.Add(_title);
        Controls.Add(icon);

        _back.Click += (_, _) => Go(_step - 1);
        _next.Click += (_, _) =>
        {
            if (_step == 3) { Close(); return; }
            if (_step == 1 && string.IsNullOrWhiteSpace(_dir.Text.Trim()))
            {
                MessageBox.Show("请选择安装位置。", "提示");
                return;
            }
            if (_step == 2) { InstallNow(); return; }
            Go(_step + 1);
        };
        _cancel.Click += (_, _) => Close();

        Go(0);
    }

    private void AddBody(Control c)
    {
        _body.Controls.Clear();
        c.Location = new Point(8, 10);
        _body.Controls.Add(c);
    }

    private void Go(int step)
    {
        _step = step;
        switch (step)
        {
            case 0:
                _title.Text = "欢迎使用 Nexus History Downloader";
                _subtitle.Text = "安装向导将引导你完成安装。";
                AddBody(new Label
                {
                    Text = "本程序将安装：\n\n  · Nexus History Downloader 桌面应用\n  · 多线程下载引擎（内含在应用中）\n\n单击「下一步」继续。",
                    AutoSize = true,
                });
                _back.Enabled = false;
                _next.Enabled = true;
                _next.Text = "下一步";
                break;
            case 1:
                _title.Text = "选择安装位置";
                _subtitle.Text = "程序与数据将安装到该文件夹。";
                if (string.IsNullOrWhiteSpace(_dir.Text))
                {
                    _dir.Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                             "Programs", "NHD");
                }
                var pick = new Button { Text = "浏览…", Size = new Size(86, 26) };
                pick.Click += (_, _) =>
                {
                    using var fbd = new FolderBrowserDialog { Description = "选择安装位置", ShowNewFolderButton = true };
                    if (fbd.ShowDialog() == DialogResult.OK) _dir.Text = Program.NormalizeDir(fbd.SelectedPath);
                };
                var row = new FlowLayoutPanel { FlowDirection = FlowDirection.LeftToRight, AutoSize = true };
                row.Controls.Add(new Label { Text = "安装位置", AutoSize = true, Width = 70 });
                row.Controls.Add(_dir);
                row.Controls.Add(pick);
                AddBody(row);
                _back.Enabled = true;
                _next.Text = "下一步";
                break;
            case 2:
                _title.Text = "安装选项";
                _subtitle.Text = "确认设置，然后开始安装。";
                var ops = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true };
                ops.Controls.Add(_shortcut);
                ops.Controls.Add(new Label
                {
                    Text = "安装位置：{0}\n若需修改，请点「上一步」。".Replace("{0}", Program.NormalizeDir(_dir.Text.Trim())),
                    AutoSize = true,
                    ForeColor = Color.DimGray,
                });
                AddBody(ops);
                _back.Enabled = true;
                _next.Text = "安装";
                break;
            default:
                _title.Text = "正在完成安装…";
                _subtitle.Text = "";
                AddBody(_result);
                _back.Enabled = false;
                _next.Enabled = true;
                _next.Text = "完成";
                break;
        }
    }

    private void InstallNow()
    {
        _next.Enabled = false;
        _back.Enabled = false;
        AddBody(new Label { Text = "正在安装，请稍候…", AutoSize = true });
        Application.DoEvents();
        string? err = Program.Install(Program.NormalizeDir(_dir.Text.Trim()), _shortcut.Checked, out var target);
        Go(3);
        _result.Text = err is null
            ? "安装完成。\n\n安装位置：{0}\n\n可从安装位置或桌面快捷方式启动 NHD.exe。".Replace("{0}", target)
            : "安装失败：{0}".Replace("{0}", err);
    }
}
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

namespace NexusUninstall
{
    // Uses the .NET Framework included with Windows 10/11; no bundled runtime or app payload.
    public static class Program
    {
        private const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\NHD";

        [STAThread]
        private static int Main(string[] args)
        {
            try
            {
                string self = Path.GetFullPath(Assembly.GetExecutingAssembly().Location);
                string dir = Path.GetDirectoryName(self);
                bool silent = args.Length == 2 && args[0] == "--uninstall";
                if (args.Length != 0 && !silent)
                    throw new ArgumentException("不支持的卸载参数。");
                if (silent && !SamePath(args[1], dir))
                    throw new InvalidOperationException("只能卸载此卸载程序所在的安装文件夹。");
                ValidateInstallDirectory(dir);
                if (!silent && MessageBox.Show(
                    "确定要卸载 Nexus History Downloader 吗？\n卸载将删除安装文件夹中的全部内容（含下载文件与登录会话）。",
                    "卸载", MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
                    return 0;

                // Load this tiny assembly into memory before deleting it. Paths are passed as data,
                // never interpolated into shell code, and no temporary copy is left behind.
                string script = @"
$ErrorActionPreference = 'Stop'
try {
    $assembly = [Reflection.Assembly]::Load([IO.File]::ReadAllBytes($env:NHD_UNINSTALL_SOURCE))
    [NexusUninstall.Program]::RunUninstall([string]$env:NHD_UNINSTALL_DIRECTORY, [int]$env:NHD_UNINSTALL_PID)
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.GetBaseException().Message, '卸载失败') | Out-Null
    exit 1
}";
                var start = new ProcessStartInfo(
                    Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System),
                                 @"WindowsPowerShell\v1.0\powershell.exe"),
                    "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " +
                    Convert.ToBase64String(Encoding.Unicode.GetBytes(script)));
                start.UseShellExecute = false;
                start.CreateNoWindow = true;
                start.WorkingDirectory = Path.GetTempPath();
                start.EnvironmentVariables["NHD_UNINSTALL_SOURCE"] = self;
                start.EnvironmentVariables["NHD_UNINSTALL_DIRECTORY"] = dir;
                start.EnvironmentVariables["NHD_UNINSTALL_PID"] = Process.GetCurrentProcess().Id.ToString();
                using (Process worker = Process.Start(start))
                    if (worker == null) throw new IOException("无法启动卸载进程。");
                return 0;
            }
            catch (Exception ex)
            {
                MessageBox.Show(ex.Message, "卸载失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }

        // Invoked by the in-memory copy in Windows PowerShell after the original executable exits.
        public static void RunUninstall(string directory, int launcherPid)
        {
            try
            {
                using (Process launcher = Process.GetProcessById(launcherPid))
                    if (!launcher.WaitForExit(15000))
                        throw new IOException("卸载程序仍在运行，请稍后重试。");
            }
            catch (ArgumentException) { /* The launcher has already exited. */ }

            string dir = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar);
            ValidateInstallDirectory(dir);
            // Check the app before removing data so an open app produces a recoverable error.
            using (new FileStream(Path.Combine(dir, "NHD.exe"), FileMode.Open, FileAccess.ReadWrite, FileShare.None)) { }

            DeleteContents(new DirectoryInfo(dir));
            Directory.Delete(dir, false);

            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(UninstallKey))
            {
                string registeredDir = key == null ? null : key.GetValue("InstallLocation") as string;
                // Another installation may have replaced this per-user entry.
                if (!string.IsNullOrEmpty(registeredDir) && SamePath(registeredDir, dir))
                {
                    string shortcut = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "NHD.lnk");
                    if (File.Exists(shortcut)) File.Delete(shortcut);
                    Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
                }
            }
        }

        private static bool SamePath(string left, string right)
        {
            return string.Equals(Path.GetFullPath(left).TrimEnd('\\', '/'),
                                 Path.GetFullPath(right).TrimEnd('\\', '/'),
                                 StringComparison.OrdinalIgnoreCase);
        }

        private static void ValidateInstallDirectory(string path)
        {
            if (!Path.IsPathRooted(path) || path.StartsWith(@"\\", StringComparison.Ordinal))
                throw new InvalidOperationException("安装路径必须是本地绝对路径。");
            var dir = new DirectoryInfo(Path.GetFullPath(path));
            if (dir.Parent == null || !dir.Name.Equals("NHD", StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(Path.Combine(dir.FullName, "NHD.exe")) ||
                !File.Exists(Path.Combine(dir.FullName, "Uninstall.exe")))
                throw new InvalidOperationException("未找到有效的 NHD 安装文件夹，已停止卸载。");
            // Reject linked installation paths; child links are removed without following them.
            for (DirectoryInfo current = dir; current != null; current = current.Parent)
                if ((current.Attributes & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidOperationException("安装路径包含目录链接，请从真实安装路径卸载。");
        }

        private static void DeleteContents(DirectoryInfo dir)
        {
            foreach (FileSystemInfo entry in dir.GetFileSystemInfos())
            {
                var child = entry as DirectoryInfo;
                if (child != null && (child.Attributes & FileAttributes.ReparsePoint) == 0)
                    DeleteContents(child);
                if ((entry.Attributes & FileAttributes.ReadOnly) != 0)
                    entry.Attributes &= ~FileAttributes.ReadOnly;
                entry.Delete();
            }
        }
    }
}

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Security.Cryptography;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("ZYLO Desktop Assistant Setup")]
[assembly: AssemblyProduct("ZYLO Desktop Assistant")]
[assembly: AssemblyVersion("2.5.0.0")]

internal sealed class ZyloSetup : Form
{
    private const string AppUserModelId = "ZYLO.DesktopAssistant";
    private readonly Button install = new Button { Text = "Install", Width = 100, Height = 32 };
    private readonly CheckBox launch = new CheckBox { Text = "Open ZYLO after installation", Checked = true, AutoSize = true };
    private readonly TextBox output = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill };
    private readonly Label status = new Label { Text = "Ready to install", AutoSize = true };
    private readonly BackgroundWorker worker = new BackgroundWorker { WorkerReportsProgress = true };

    [STAThread]
    private static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (String.Equals(Path.GetFileName(Application.ExecutablePath), "ZYLO.exe", StringComparison.OrdinalIgnoreCase))
        {
            try { Process.Start(PythonLaunchInfo(AppDomain.CurrentDomain.BaseDirectory)); }
            catch (Exception ex) { MessageBox.Show("Could not open ZYLO. " + ex.Message, "ZYLO Desktop Assistant", MessageBoxButtons.OK, MessageBoxIcon.Error); }
            return;
        }
        Application.Run(new ZyloSetup());
    }

    private ZyloSetup()
    {
        Text = "ZYLO Desktop Assistant Setup";
        Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(520, 390);
        ClientSize = new Size(600, 380);
        Font = new Font("Segoe UI", 10);
        var layout = new TableLayoutPanel { ColumnCount = 1, RowCount = 5, Dock = DockStyle.Fill, Padding = new Padding(20) };
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.Controls.Add(new Label { Text = "Install or update ZYLO Desktop Assistant", AutoSize = true, Font = new Font("Segoe UI", 17, FontStyle.Bold) }, 0, 0);
        layout.Controls.Add(new Label {
            Text = "Installs for your Windows account and reuses compatible ZYLO dependencies when available.",
            AutoSize = true, MaximumSize = new Size(510, 0), Margin = new Padding(3, 8, 3, 12)
        }, 0, 1);
        layout.Controls.Add(output, 0, 2);
        status.Margin = new Padding(3, 8, 3, 4);
        layout.Controls.Add(status, 0, 3);
        var actions = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, FlowDirection = FlowDirection.RightToLeft, WrapContents = false };
        actions.Controls.Add(install);
        launch.Margin = new Padding(3, 9, 12, 3);
        actions.Controls.Add(launch);
        layout.Controls.Add(actions, 0, 4);
        Controls.Add(layout);
        AcceptButton = install;
        install.Click += delegate {
            if (install.Text == "Close") { Close(); return; }
            output.Clear();
            status.Text = "Installing...";
            install.Enabled = false;
            launch.Enabled = false;
            worker.RunWorkerAsync();
        };
        FormClosing += delegate(object sender, FormClosingEventArgs e) { e.Cancel = worker.IsBusy; };
        worker.DoWork += delegate(object sender, DoWorkEventArgs e) {
            e.Result = InstallApp(message => worker.ReportProgress(0, message));
        };
        worker.ProgressChanged += delegate(object sender, ProgressChangedEventArgs e) {
            output.AppendText((string)e.UserState + Environment.NewLine);
        };
        worker.RunWorkerCompleted += Finished;
    }

    private void Finished(object sender, RunWorkerCompletedEventArgs e)
    {
        install.Enabled = true;
        if (e.Error != null)
        {
            status.Text = "Installation stopped. You can retry.";
            output.AppendText(Environment.NewLine + e.Error.Message + Environment.NewLine);
            launch.Enabled = true;
            install.Text = "Retry";
            return;
        }
        string appPath = (string)e.Result;
        try
        {
            CreateShortcut(appPath, Environment.GetFolderPath(Environment.SpecialFolder.Programs));
            status.Text = "Installed. Open ZYLO from the Start menu.";
        }
        catch (Exception ex)
        {
            status.Text = "Installed. Windows could not create the Start menu shortcut.";
            output.AppendText(ex.Message + Environment.NewLine + "Installation folder: " + appPath + Environment.NewLine);
        }
        install.Text = "Close";
        if (launch.Checked)
        {
            try { Process.Start(LaunchInfo(appPath)); }
            catch (Exception ex) {
                output.AppendText("Windows could not open ZYLO: " + ex.Message + Environment.NewLine);
            }
        }
    }

    private static string InstallApp(Action<string> log)
    {
        using (var setupLock = new Mutex(false, @"Local\ZYLODesktopAssistantSetup"))
        {
            bool ownsLock;
            try { ownsLock = setupLock.WaitOne(0); }
            catch (AbandonedMutexException) { ownsLock = true; }
            if (!ownsLock) throw new InvalidOperationException("Another ZYLO installer is running.");
            try
            {
                Mutex activeApp;
                if (Mutex.TryOpenExisting(@"Local\ZYLODesktopAssistantWin11", System.Security.AccessControl.MutexRights.Synchronize, out activeApp))
                {
                    activeApp.Dispose();
                    throw new InvalidOperationException("Quit ZYLO from its notification area icon and click Retry.");
                }
                log("Checking for 64-bit Python...");
                string python = FindPython();
                log("Using " + python);
                string basePath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "ZYLO");
                string appPath = UnpackRelease(basePath);
                string runtime = FindInstalledRuntime(appPath, basePath);
                if (runtime == null)
                {
                    string envPath = Path.Combine(appPath, ".venv");
                    runtime = Path.Combine(envPath, "Scripts", "python.exe");
                    if (!File.Exists(runtime) || Run(runtime, "-I -m pip --version", appPath, null) != 0)
                        RunChecked(python, "-I -m venv " + Quote(envPath), appPath, log, "Could not create the Python environment.");
                    InstallDependencies(runtime, appPath, log);
                    RunChecked(runtime, ImportCheck, appPath, log, "ZYLO could not load its application modules.");
                }
                else
                {
                    log("Reusing the installed ZYLO dependencies.");
                }
                string runtimeWindowed = Path.Combine(Path.GetDirectoryName(runtime), "pythonw.exe");
                File.WriteAllText(Path.Combine(appPath, "python-runtime.txt"), runtimeWindowed);
                File.Copy(Application.ExecutablePath, Path.Combine(appPath, "ZYLO.exe"), true);
                PrepareProfile(runtime, appPath, log);
                return appPath;
            }
            finally { setupLock.ReleaseMutex(); }
        }
    }

    private static readonly string ImportCheck = "-E -s -c " + Quote("import zylo_win11, setup_app");

    private static void PrepareProfile(string runtime, string appPath, Action<string> log)
    {
        string command = "from step_desklet.core.config import refresh_local_date; " +
            "from step_desklet.core.skills import ensure_skills_directory; " +
            "ensure_skills_directory(); refresh_local_date()";
        if (Run(runtime, "-E -s -B -c " + Quote(command), appPath, log) != 0)
            log("ZYLO will refresh the bundled skills and local date when it starts.");
    }

    private static string FindInstalledRuntime(string appPath, string basePath)
    {
        string requirements = File.ReadAllText(Path.Combine(appPath, "requirements.txt"));
        string constraints = File.ReadAllText(Path.Combine(appPath, "requirements-lock.txt"));
        foreach (string release in Directory.GetDirectories(basePath, "app-*"))
        {
            string scripts = Path.Combine(release, ".venv", "Scripts");
            string runtime = Path.Combine(scripts, "python.exe");
            string otherRequirements = Path.Combine(release, "requirements.txt");
            string otherConstraints = Path.Combine(release, "requirements-lock.txt");
            if (!File.Exists(runtime) || !File.Exists(Path.Combine(scripts, "pythonw.exe")) ||
                !File.Exists(otherRequirements) || !File.Exists(otherConstraints) ||
                File.ReadAllText(otherRequirements) != requirements || File.ReadAllText(otherConstraints) != constraints)
                continue;
            try
            {
                if (Run(runtime, "-I -m pip check", appPath, null) == 0 && Run(runtime, ImportCheck, appPath, null) == 0)
                    return runtime;
            }
            catch (Win32Exception) { }
        }
        return null;
    }

    private static void InstallDependencies(string runtime, string appPath, Action<string> log)
    {
        log("Installing ZYLO's Python dependencies...");
        RunChecked(runtime, "-I -m pip install --disable-pip-version-check --no-input --retries 2 --timeout 15 -r requirements.txt -c requirements-lock.txt",
            appPath, log, "Dependency installation failed. Check the error above and click Retry.");
        RunChecked(runtime, "-I -m pip check", appPath, log, "The installed dependencies failed their compatibility check.");
    }

    private static string UnpackRelease(string basePath)
    {
        using (Stream payload = Assembly.GetExecutingAssembly().GetManifestResourceStream("ZyloPayload.zip"))
        using (var sha = SHA256.Create())
        {
            string releaseId = BitConverter.ToString(sha.ComputeHash(payload)).Replace("-", "").Substring(0, 16).ToLowerInvariant();
            string root = Path.GetFullPath(Path.Combine(basePath, "app-" + releaseId));
            string prefix = root + Path.DirectorySeparatorChar;
            Directory.CreateDirectory(root);
            payload.Position = 0;
            using (var archive = new ZipArchive(payload, ZipArchiveMode.Read))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string target = Path.GetFullPath(Path.Combine(root, entry.FullName));
                    if (!target.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("An embedded file would extract outside the installation folder.");
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    using (Stream input = entry.Open())
                    using (var destination = File.Create(target))
                        input.CopyTo(destination);
                }
            }
            return root;
        }
    }

    private static string FindPython()
    {
        var candidates = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var folders = new List<string>((Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'));
        var common = new[] {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "AppData", "Local", "Programs", "Python"),
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles)
        };
        foreach (string parent in common)
        {
            if (!Directory.Exists(parent)) continue;
            try { folders.AddRange(Directory.GetDirectories(parent, "Python3*")); }
            catch (UnauthorizedAccessException) { }
        }
        foreach (string path in folders)
        {
            string folder = path.Trim().Trim('"');
            if (!Path.IsPathRooted(folder) || folder.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0)
                continue;
            string candidate = Path.Combine(folder, "python.exe");
            if (!candidates.Add(candidate) || !File.Exists(candidate) || !File.Exists(Path.Combine(folder, "pythonw.exe")))
                continue;
            try
            {
                if (Run(candidate, "-I -c " + Quote("import ensurepip, sys, sysconfig; sys.exit(0 if sys.version_info >= (3, 11) and sysconfig.get_platform() == 'win-amd64' else 1)"),
                    Environment.GetFolderPath(Environment.SpecialFolder.Windows), null) == 0)
                    return candidate;
            }
            catch (Win32Exception) { }
        }
        throw new InvalidOperationException("Install 64-bit Python 3.11 or newer from python.org, then click Retry.");
    }

    private static ProcessStartInfo LaunchInfo(string appPath)
    {
        return new ProcessStartInfo(Path.Combine(appPath, "ZYLO.exe")) {
            WorkingDirectory = appPath, UseShellExecute = false
        };
    }

    private static ProcessStartInfo PythonLaunchInfo(string appPath)
    {
        string runtime = File.ReadAllText(Path.Combine(appPath, "python-runtime.txt")).Trim();
        return new ProcessStartInfo(runtime, Quote(Path.Combine(appPath, "zylo_win11.py"))) {
            WorkingDirectory = appPath, UseShellExecute = false
        };
    }

    private static void CreateShortcut(string appPath, string programs)
    {
        Directory.CreateDirectory(programs);
        var start = LaunchInfo(appPath);
        dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell", true));
        string shortcutPath = Path.Combine(programs, "ZYLO Desktop Assistant.lnk");
        dynamic shortcut = shell.CreateShortcut(shortcutPath);
        shortcut.TargetPath = start.FileName;
        shortcut.Arguments = start.Arguments;
        shortcut.WorkingDirectory = start.WorkingDirectory;
        shortcut.Description = "Open ZYLO Desktop Assistant";
        shortcut.IconLocation = start.FileName + ",0";
        shortcut.Save();
        SetShortcutAppId(shortcutPath);
    }

    private static void SetShortcutAppId(string shortcutPath)
    {
        var shellLinkId = new Guid("00021401-0000-0000-C000-000000000046");
        object link = Activator.CreateInstance(Type.GetTypeFromCLSID(shellLinkId));
        var value = new PropVariant { Type = 31, Pointer = Marshal.StringToCoTaskMemUni(AppUserModelId) };
        try
        {
            var file = (IPersistFile)link;
            const int ReadWrite = 2; // STGM_READWRITE
            file.Load(shortcutPath, ReadWrite);
            var store = (IPropertyStore)link;
            var key = new PropertyKey { FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), Id = 5 };
            store.SetValue(ref key, ref value);
            store.Commit();
            file.Save(shortcutPath, true);
        }
        finally
        {
            Marshal.FreeCoTaskMem(value.Pointer);
            Marshal.FinalReleaseComObject(link);
        }
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    private struct PropertyKey
    {
        public Guid FormatId;
        public uint Id;
    }

    // VT_LPWSTR uses the pointer at offset 8 in the x64 PROPVARIANT union.
    [StructLayout(LayoutKind.Explicit, Size = 24)]
    private struct PropVariant
    {
        [FieldOffset(0)] public ushort Type;
        [FieldOffset(8)] public IntPtr Pointer;
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPropertyStore
    {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, out PropVariant value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }

    private static void RunChecked(string executable, string args, string folder, Action<string> log, string error)
    {
        if (Run(executable, args, folder, log) != 0) throw new InvalidOperationException(error);
    }

    private static int Run(string executable, string args, string folder, Action<string> log)
    {
        using (var process = new Process())
        {
            process.StartInfo = new ProcessStartInfo(executable, args) {
                WorkingDirectory = folder, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true
            };
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null && log != null) log(e.Data); };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null && log != null) log(e.Data); };
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            process.WaitForExit();
            return process.ExitCode;
        }
    }

    private static string Quote(string value) { return "\"" + value + "\""; }
}

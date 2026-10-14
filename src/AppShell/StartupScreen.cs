using System.Diagnostics;
using System.Drawing.Drawing2D;
using System.Drawing.Text;

namespace NexusApp;

/// <summary>在后台和网页准备好之前显示的原生启动画面。</summary>
internal sealed class StartupScreen : Control
{
    private readonly System.Windows.Forms.Timer _animation = new() { Interval = 16 };
    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private readonly Button _retry = new();
    private int _progress;
    private float _displayProgress;
    private double _lastFrame;
    private string _status = "正在准备启动";
    private string? _error;

    public event EventHandler? RetryRequested;

    public StartupScreen()
    {
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
                 ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
        BackColor = Color.FromArgb(17, 17, 17);
        ForeColor = Color.FromArgb(237, 237, 237);
        AccessibleName = "NHD 启动进度";
        _retry.Text = "重试";
        _retry.Size = new Size(100, 36);
        _retry.FlatStyle = FlatStyle.Flat;
        _retry.FlatAppearance.BorderColor = Color.FromArgb(72, 72, 72);
        _retry.BackColor = Color.FromArgb(37, 37, 37);
        _retry.ForeColor = ForeColor;
        _retry.UseVisualStyleBackColor = false;
        _retry.Visible = false;
        _retry.Click += (_, _) => RetryRequested?.Invoke(this, EventArgs.Empty);
        Controls.Add(_retry);
        _animation.Tick += (_, _) =>
        {
            var now = _clock.Elapsed.TotalSeconds;
            var delta = Math.Min(now - _lastFrame, .05);
            _lastFrame = now;
            _displayProgress = Math.Min(_progress, _displayProgress + (float)(delta * 95));
            Invalidate();
        };
        _animation.Start();
    }

    public void SetProgress(int progress, string status)
    {
        if (IsDisposed) return;
        if (_error is not null)
        {
            _error = null;
            _progress = 0;
            _displayProgress = 0;
            _retry.Visible = false;
        }
        _progress = Math.Clamp(Math.Max(_progress, progress), 0, 100);
        _status = status;
        AccessibleDescription = $"{_progress}% · {status}";
        Invalidate();
    }

    public void ShowError(string message)
    {
        _error = message;
        _status = "启动遇到问题";
        AccessibleDescription = message;
        _retry.Visible = true;
        PositionRetry();
        Invalidate();
    }

    public async Task FinishAsync(CancellationToken cancellationToken)
    {
        SetProgress(100, "准备就绪");
        while (_displayProgress < 100)
            await Task.Delay(16, cancellationToken);
        await Task.Delay(140, cancellationToken);
    }

    protected override void OnVisibleChanged(EventArgs e)
    {
        base.OnVisibleChanged(e);
        _lastFrame = _clock.Elapsed.TotalSeconds;
        if (Visible) _animation.Start(); else _animation.Stop();
    }

    protected override void OnResize(EventArgs e)
    {
        base.OnResize(e);
        PositionRetry();
    }

    private void PositionRetry()
    {
        var scale = Math.Max(.4f, Math.Min(DeviceDpi / 96f, Math.Min(Width / 560f, Height / 600f)));
        _retry.Location = new Point((Width - _retry.Width) / 2, (int)(Height / 2f + 218 * scale));
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
        var scale = Math.Min(DeviceDpi / 96f, Math.Min(Width / 560f, Height / 600f));
        scale = Math.Max(.4f, scale);
        g.TranslateTransform(Width / 2f, Height / 2f - 18 * scale);
        g.ScaleTransform(scale, scale);
        var seconds = (float)_clock.Elapsed.TotalSeconds;

        // 细网格和同心轨道保持低对比，避免启动时出现大面积亮色。
        using var grid = new Pen(Color.FromArgb(25, 25, 25));
        for (var x = -300; x <= 300; x += 40) g.DrawLine(grid, x, -240, x, 240);
        for (var y = -240; y <= 240; y += 40) g.DrawLine(grid, -300, y, 300, y);
        using var backdrop = new GraphicsPath();
        backdrop.AddEllipse(-260, -230, 520, 460);
        using var glow = new PathGradientBrush(backdrop)
        {
            CenterColor = Color.FromArgb(30, 30, 30),
            SurroundColors = [Color.FromArgb(17, 17, 17)],
        };
        g.FillPath(glow, backdrop);

        DrawText(g, "NHD", 25, FontStyle.Bold, Color.FromArgb(224, 224, 224), -224, 44);
        DrawText(g, "NEXUS HISTORY DOWNLOADER", 9, FontStyle.Regular,
            Color.FromArgb(110, 110, 110), -183, 20);

        var ring = new RectangleF(-84, -105, 168, 168);
        using var track = new Pen(Color.FromArgb(48, 48, 48), 3);
        using var progress = new Pen(Color.FromArgb(205, 205, 205), 3)
            { StartCap = LineCap.Round, EndCap = LineCap.Round };
        g.DrawEllipse(track, ring);
        if (_displayProgress > 0) g.DrawArc(progress, ring, -90, _displayProgress * 3.6f);

        for (var i = 0; i < 60; i++)
        {
            var angle = i * MathF.PI / 30;
            var length = i % 5 == 0 ? 6 : 3;
            var lit = i < _displayProgress * .6f;
            using var tick = new Pen(Color.FromArgb(lit ? 118 : 49, lit ? 118 : 49, lit ? 118 : 49));
            g.DrawLine(tick, MathF.Sin(angle) * 100, -MathF.Cos(angle) * 100 - 21,
                MathF.Sin(angle) * (100 + length), -MathF.Cos(angle) * (100 + length) - 21);
        }
        using var orbit = new Pen(Color.FromArgb(76, 76, 76), 1);
        using var satellite = new Pen(Color.FromArgb(140, 140, 140), 2)
            { StartCap = LineCap.Round, EndCap = LineCap.Round };
        g.DrawArc(orbit, -119, -140, 238, 238, seconds * 18, 65);
        g.DrawArc(orbit, -119, -140, 238, 238, seconds * 18 + 180, 65);
        if (_error is null)
            g.DrawArc(satellite, -110, -131, 220, 220, -seconds * 44, 18);

        DrawText(g, $"{(int)_displayProgress}%", 34, FontStyle.Regular, ForeColor, -54, 54);
        DrawText(g, _error is null ? "LOADING" : "PAUSED", 8, FontStyle.Regular,
            Color.FromArgb(117, 117, 117), 4, 20);
        DrawText(g, _status, 12, FontStyle.Regular, Color.FromArgb(196, 196, 196), 126, 28);
        DrawText(g, _error ?? "正在准备你的模组工作台", 10, FontStyle.Regular,
            Color.FromArgb(121, 121, 121), 164, _error is null ? 24 : 60);

        using var rail = new Pen(Color.FromArgb(48, 48, 48), 2);
        using var fill = new Pen(Color.FromArgb(170, 170, 170), 2);
        g.DrawLine(rail, -140, 108, 140, 108);
        g.DrawLine(fill, -140, 108, -140 + 280 * _displayProgress / 100, 108);
        for (var i = 1; i < 5; i++)
        {
            using var divider = new Pen(BackColor, 4);
            g.DrawLine(divider, -140 + 56 * i, 104, -140 + 56 * i, 112);
        }
    }

    private static void DrawText(Graphics graphics, string text, float size, FontStyle style,
        Color color, float top, float height)
    {
        using var font = new Font("Microsoft YaHei UI", size, style, GraphicsUnit.Pixel);
        using var brush = new SolidBrush(color);
        using var format = new StringFormat { Alignment = StringAlignment.Center,
            LineAlignment = StringAlignment.Center, Trimming = StringTrimming.EllipsisCharacter };
        graphics.DrawString(text, font, brush, new RectangleF(-230, top, 460, height), format);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) _animation.Dispose();
        base.Dispose(disposing);
    }
}

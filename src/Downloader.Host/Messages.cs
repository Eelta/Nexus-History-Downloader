using System.Text.Json;

namespace CustomFileDownloader.DownloaderHost;

/// <summary>
/// 接管下载请求契约（camelCase JSON，与 Python 侧 takeover.py 保持一致）。
/// url 必填；cookie/referrer/userAgent 可选（浏览器会话转交时携带）。
/// </summary>
public sealed record TakeoverRequest
{
    public string Type { get; init; } = "download";
    public string Url { get; init; } = string.Empty;
    public string? Filename { get; init; }
    public string? Cookie { get; init; }
    public string? Referrer { get; init; }
    public string? UserAgent { get; init; }
    /// <summary>本次下载的临时目录（可选；未提供时用 settings.json 的默认目录）。</summary>
    public string? SaveDir { get; init; }
    /// <summary>本次任务的并发线程数（可选；未提供时用全局设置/滑块值）。</summary>
    public int? MaxThreads { get; init; }
}

/// <summary>提交结果回执。</summary>
public sealed record TakeoverResponse
{
    public bool Ok { get; init; }
    public string Message { get; init; } = string.Empty;
    public string? JobId { get; init; }
}

public static class NativeJson
{
    /// <summary>Web 默认：camelCase 序列化 + 属性名大小写不敏感反序列化。</summary>
    public static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web);

    public static string Serialize<T>(T value) => JsonSerializer.Serialize(value, Options);
    public static T? Deserialize<T>(string json) => JsonSerializer.Deserialize<T>(json, Options);
}

/// <summary>单个下载任务的快照（用于 HTTP /api/jobs 与 JSONL 进度文件）。</summary>
public sealed record JobSnapshot
{
    public string Id { get; init; } = string.Empty;
    public string Url { get; init; } = string.Empty;
    public string Filename { get; init; } = string.Empty;
    public string SavePath { get; init; } = string.Empty;
    public string State { get; init; } = "queued";   // queued|downloading|done|error
    public long Downloaded { get; init; }
    public long? Total { get; init; }
    public double Speed { get; init; }
    public int ActiveThreads { get; init; }
    public int StartedThreads { get; init; }
    public string? Error { get; init; }
    public string? StartedAt { get; init; }
    public string? FinishedAt { get; init; }
    /// <summary>平均速度（字节/秒，完成时按 总量/用时 计算）。</summary>
    public double AvgSpeedBps { get; init; }
    /// <summary>峰值速度（字节/秒）。</summary>
    public double PeakSpeedBps { get; init; }
    /// <summary>实际用时（秒）。</summary>
    public double ElapsedSeconds { get; init; }
    /// <summary>实际最大并发连接数（受限线程数上限）。</summary>
    public int MaxConcurrent { get; init; }
}
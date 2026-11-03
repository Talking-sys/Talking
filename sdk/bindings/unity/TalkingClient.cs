// Talking game-embed SDK — Unity / C# interface sketch (not a shippable plugin).
// Planned binding: wrap UnityWebRequest around POST /api/sdk/v1/*.
// Licence of this sketch: MIT (see sdk/LICENSE). Do not embed API keys in builds;
// load them from a server-issued short-lived token or player prefs that the
// studio injects at runtime.
//
// Namespace is sketched only — this file is not compiled by the Python CI.

using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace Talking.Sdk
{
    public sealed class TalkingConfig
    {
        public string ServerUrl;          // e.g. https://talk.example
        public string ApiKey;             // never commit; never log
        public string AppId;
        public float TimeoutSeconds = 15f;
        public int MaxRetries = 3;
        public bool Offline;
        public string TargetLanguage;
    }

    public sealed class LanguageInfo
    {
        public string Code;
        public string EnglishName;
        public string RussianName;
        public string Label;
    }

    public sealed class TranslationResult
    {
        public string RequestId;
        public string Source;
        public string Target;
        public string Text;
        public string Translation;
        public string Provider;
        public int ElapsedMs;
        public byte[] Audio;              // null unless returnAudio
        public string AudioMime;
    }

    /// <summary>
    /// Async-friendly surface that a future Unity package should implement.
    /// Streaming: subscribe to TranslationStream for SSE once the C# parser ships.
    /// </summary>
    public interface ITalkingClient : IDisposable
    {
        void SetTargetLanguage(string code);
        string GetTargetLanguage();
        Task<IReadOnlyList<LanguageInfo>> ListLanguagesAsync(CancellationToken ct = default);
        Task<TranslationResult> TranslateTextAsync(string text, string source = "auto",
            string target = null, bool returnAudio = false, CancellationToken ct = default);
        Task<TranslationResult> TranslateAudioAsync(byte[] audio, string format = "wav",
            string source = "auto", string target = null, bool returnAudio = false,
            int sampleRate = 16000, CancellationToken ct = default);
    }

    // Placeholder so game programmers can see the intended constructor.
    public sealed class TalkingClient : ITalkingClient
    {
        public TalkingClient(TalkingConfig config)
        {
            if (config == null) throw new ArgumentNullException(nameof(config));
            if (string.IsNullOrWhiteSpace(config.ServerUrl))
                throw new ArgumentException("ServerUrl is required");
            if (string.IsNullOrWhiteSpace(config.ApiKey))
                throw new ArgumentException("ApiKey is required");
            throw new NotImplementedException(
                "Unity binding is a skeleton. Use the Python or JS SDK, or HTTP as in docs/sdk/PLAN.md.");
        }

        public void SetTargetLanguage(string code) => throw new NotImplementedException();
        public string GetTargetLanguage() => throw new NotImplementedException();
        public Task<IReadOnlyList<LanguageInfo>> ListLanguagesAsync(CancellationToken ct = default)
            => throw new NotImplementedException();
        public Task<TranslationResult> TranslateTextAsync(string text, string source = "auto",
            string target = null, bool returnAudio = false, CancellationToken ct = default)
            => throw new NotImplementedException();
        public Task<TranslationResult> TranslateAudioAsync(byte[] audio, string format = "wav",
            string source = "auto", string target = null, bool returnAudio = false,
            int sampleRate = 16000, CancellationToken ct = default)
            => throw new NotImplementedException();
        public void Dispose() {}
    }
}

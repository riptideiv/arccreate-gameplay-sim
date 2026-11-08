// CLI adapter for ArcCreate 1.2.68. Gameplay and encoding use ArcCreate's own exporter.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;
using Newtonsoft.Json;
using ArcCreate;
using ArcCreate.Compose;
using ArcCreate.Compose.Components;
using ArcCreate.Compose.Rendering;
using ArcCreate.Gameplay;
using ArcCreate.SceneTransition;
using Cysharp.Threading.Tasks;
using UnityEngine;

public static class Export
{
    private class ViewOptions { public float scroll_speed = 5; }
    public static void Start(ComposeManager manager)
    {
        string[] args = Environment.GetCommandLineArgs();
        int flag = Array.IndexOf(args, "--export-aff-video");
        if (flag < 0) return;
        // Chart zero can precede song zero when AudioOffset is negative.
        // Native rendering/audio slicing already handle that silent lead-in.
        int from, to;
        if (args.Length <= flag + 4 || !int.TryParse(args[flag + 2], out from)
            || !int.TryParse(args[flag + 3], out to) || to <= from)
        {
            Debug.LogError("Expected --export-aff-video OUTPUT START_MS END_MS FFMPEG.");
            Application.Quit(2);
            return;
        }
        manager.StartCoroutine(Guard(Run(args[flag + 1], from, to), args[flag + 4]));
    }

    private static IEnumerator Guard(IEnumerator export, string ffmpeg)
    {
        string previousFfmpeg = Settings.FFmpegPath.Value;
        int previousInputMode = Settings.InputMode.Value;
        int previousOffset = Settings.GlobalAudioOffset.Value;
        int previousDropRate = Settings.DropRate.Value;
        Settings.GlobalAudioOffset.Value = 0;
        Settings.FFmpegPath.Value = ffmpeg;
        Settings.InputMode.Value = (int)InputMode.Auto;
        try
        {
            while (true)
            {
                object step;
                try
                {
                    if (!export.MoveNext()) yield break;
                    step = export.Current;
                }
                catch (Exception error)
                {
                    Debug.LogException(error);
                    Application.Quit(1);
                    yield break;
                }
                yield return step;
            }
        }
        finally
        {
            Settings.FFmpegPath.Value = previousFfmpeg;
            Settings.InputMode.Value = previousInputMode;
            Settings.GlobalAudioOffset.Value = previousOffset;
            Settings.DropRate.Value = previousDropRate;
            (export as IDisposable)?.Dispose();
        }
    }

    private static IEnumerator Run(string output, int from, int to)
    {
        Application.runInBackground = true;
        string[] optionsArgs = Environment.GetCommandLineArgs();
        int inputFlag = Array.IndexOf(optionsArgs, "--touch-replay");
        var view = inputFlag >= 0 ? JsonConvert.DeserializeObject<ViewOptions>(File.ReadAllText(optionsArgs[inputFlag + 1])) : new ViewOptions();
        if (float.IsNaN(view.scroll_speed) || view.scroll_speed < 1 || view.scroll_speed > 10)
            throw new Exception("Scroll speed must be between 1 and 10.");
        Settings.DropRate.Value = (int)Math.Round(view.scroll_speed * 30);
        output = Path.GetFullPath(output);
        var service = UnityEngine.Object.FindObjectOfType<VideoRenderService>(true);
        var fields = BindingFlags.Instance | BindingFlags.NonPublic;
        var data = (GameplayData)typeof(VideoRenderService).GetField("gameplayData", fields).GetValue(service);
        var viewport = (GameplayViewport)typeof(VideoRenderService).GetField("gameplayViewport", fields).GetValue(service);
        var services = typeof(ComposeManager).Assembly.GetType("ArcCreate.Compose.Services");
        var gameplay = (IGameplayControl)services.GetProperty("Gameplay").GetValue(null);
        var timeout = Time.realtimeSinceStartup + 30;
        while (data.AudioClip.Value == null && Time.realtimeSinceStartup < timeout)
            yield return null;
        if (data.AudioClip.Value == null)
        {
            Debug.LogError("AFF export could not load project audio.");
            Application.Quit(1);
            yield break;
        }
        // Keep the final judgement-expiry tail when a full-song preview reaches
        // the music's end. The source audio file is never modified.
        if (to > gameplay.Audio.AudioLength)
        {
            AudioClip original = data.AudioClip.Value;
            float[] samples = new float[original.samples * original.channels];
            original.GetData(samples, 0);
            AudioClip padded = AudioClip.Create("Replay with judgement tail", (int)Math.Ceiling((to + 100) / 1000.0 * original.frequency), original.channels, original.frequency, false);
            padded.SetData(samples, 0);
            data.AudioClip.Value = padded;
        }
        yield return new WaitForSecondsRealtime(0.6f);
        string[] args = Environment.GetCommandLineArgs();
        int replay = Array.IndexOf(args, "--touch-replay");
        if (replay >= 0) Replay.Install(args[replay + 1], gameplay, data);

        var settings = new RenderSetting
        {
            Width = 1148, Height = 802, Fps = replay >= 0 ? 60 : 30, Crf = 17, MusicVolume = 1, EffectVolume = replay >= 0 ? 0 : 0.16f,
        };
        var transition = new TransitionSequence();
        var audio = new ArcCreate.Compose.Rendering.AudioRenderer(from, to, data.AudioOffset.Value,
            data.AudioClip.Value, gameplay.Audio.TapHitsoundClip, gameplay.Audio.ArcHitsoundClip,
            TransitionScene.ExternalRenderStartAudio.Value, TransitionScene.ExternalGameplayLoadCompleteAudio.Value,
            replay >= 0 ? new Dictionary<string, AudioClip>() : gameplay.Audio.SfxAudioClips, false, settings, transition);
        audio.CreateAudio();
        using (var renderer = new FrameRenderer(output, gameplay.Camera.RenderingCameras, settings, from, to,
            audio, false, viewport, transition))
        {
            var task = renderer.RenderVideo(CancellationToken.None, (passed, remaining) => { });
            while (task.Status == UniTaskStatus.Pending)
                yield return null;
            task.GetAwaiter().GetResult();
        }
        if (replay >= 0) Replay.Finish(output, from, to, data.AudioClip.Value);
        bool wroteVideo = File.Exists(output) && new FileInfo(output).Length > 1000;
        Debug.Log(wroteVideo ? "AFF export complete: " + output : "AFF export did not produce a video.");
        Application.Quit(wroteVideo ? 0 : 1);
    }
}

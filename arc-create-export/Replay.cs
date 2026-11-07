// Actual two-touch input for the unmodified ArcCreate judgement handlers.
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using ArcCreate;
using Newtonsoft.Json;
using ArcCreate.Gameplay;
using ArcCreate.Gameplay.Judgement;
using ArcCreate.Gameplay.Judgement.Input;
using ArcCreate.Gameplay.Data;
using UnityEngine;

public static class Replay
{
    [Serializable] public class Contact { public int id, down_frame, up_frame; public Vector2[] points; }
    [Serializable] public class InputFile { public float width_mm, height_mm; public int chart_start_ms, audio_offset_ms, diagnostics_start_ms, diagnostics_end_ms; public Contact[] touches; }
    [Serializable] public class Judge { public int chart_ms; public string result; }
    [Serializable] public class ArcTick { public int chart_ms, arc_start_ms, arc_end_ms, color, request_start_ms, auto_ms, expiry_ms; public float x_start, x_end, y_start, y_end; public string result; }
    [Serializable] public class Result { public int hits, misses, score, combo; public string engine = "ArcCreate touch judgement"; public List<Judge> judgements = new List<Judge>(); public List<ArcTick> arc_ticks = new List<ArcTick>(); }
    private class Sound { public int timing; public AudioClip clip; }
    public static bool Enabled;
    private static IGameplayControl gameplay;
    private static InputFile input;
    private static Result result;
    private static readonly List<Sound> sounds = new List<Sound>();
    private static readonly HashSet<string> soundKeys = new HashSet<string>();
    private static readonly List<object> diagnosticFrames = new List<object>();
    private static readonly HashSet<Arc> diagnosticArcs = new HashSet<Arc>();
    private static bool Active => Enabled && gameplay != null && gameplay.Audio.IsRendering;

    public static void Install(string path, IGameplayControl control, GameplayData data)
    {
        input = JsonConvert.DeserializeObject<InputFile>(File.ReadAllText(path));
        if (input.touches == null || input.width_mm <= 0 || input.height_mm <= 0) throw new Exception("Invalid touch replay: " + JsonConvert.SerializeObject(input) + " at " + path);
        gameplay = control;
        result = new Result();
        sounds.Clear(); soundKeys.Clear();
        diagnosticFrames.Clear(); diagnosticArcs.Clear();
        Settings.InputMode.Value = (int)InputMode.Touch;
        data.EnableAutoplayMode.Value = false;
        var judge = UnityEngine.Object.FindObjectOfType<JudgementService>(true);
        judge.RefreshInputHandler();
        typeof(JudgementService).GetField("inputHandler", BindingFlags.Instance | BindingFlags.NonPublic)
            .SetValue(judge, new ReplayInput());
        Enabled = true;
    }

    private class ReplayInput : TouchInputHandler
    {
        private int previousFrame = int.MinValue;
        public override void PollInput()
        {
            CurrentInputs.Clear();
            int frame = Mathf.RoundToInt((gameplay.Audio.ChartTiming - input.chart_start_ms + input.audio_offset_ms) * 60f / 1000);
            Camera camera = gameplay.Camera.GameplayCamera;
            foreach (var touch in input.touches)
            {
                bool down = frame >= touch.down_frame && frame < touch.up_frame;
                bool ended = previousFrame < touch.up_frame && frame >= touch.up_frame && previousFrame >= touch.down_frame;
                if (!down && !ended) continue;
                int index = Mathf.Clamp(frame - touch.down_frame, 0, touch.points.Length - 1);
                Vector2 point = touch.points[index];
                Vector3 viewport = new Vector3(point.x / input.width_mm + .5f, point.y / input.height_mm + .5f, 0);
                Vector3 screen = camera.ViewportToScreenPoint(viewport);
                bool began = down && previousFrame < touch.down_frame;
                CurrentInputs.Add(new TouchInput(touch.id, screen, began,
                    ended ? TouchPhase.Ended : began ? TouchPhase.Began : TouchPhase.Moved,
                    camera.ViewportPointToRay(viewport)));
            }
            previousFrame = frame;
            CaptureDiagnostics(gameplay.Audio.ChartTiming, CurrentInputs);
        }
    }

    // Wrap request receivers only to observe the native verdict. All input,
    // colour logic and scoring remain in the original ArcCreate handlers.
    public static ArcJudgementRequest TrackArcRequest(ArcJudgementRequest request)
    {
        if (Active && input.diagnostics_end_ms > input.diagnostics_start_ms
            && request.Arc.Timing <= input.diagnostics_end_ms && request.Arc.EndTiming >= input.diagnostics_start_ms - 1000)
            diagnosticArcs.Add(request.Arc);
        if (Active && request.IsJudgement) request.Receiver = new ArcReceiver(request);
        return request;
    }

    // Optional, read-only pre-input snapshots. Used to compare geometry and
    // ownership with the planner without changing native input or judgements.
    private static void CaptureDiagnostics(int now, List<TouchInput> touches)
    {
        if (!Active || input.diagnostics_end_ms <= input.diagnostics_start_ms
            || now < input.diagnostics_start_ms || now > input.diagnostics_end_ms) return;
        var flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var colors = new List<object>();
        for (int c = 0; c <= ArcColorLogic.MaxColor; c++)
        {
            var logic = ArcColorLogic.Get(c);
            colors.Add(new { color = c, owner = logic.AssignedFingerId,
                lock_until = (int)typeof(ArcColorLogic).GetField("lockUntil", flags).GetValue(logic),
                grace_until = (int)typeof(ArcColorLogic).GetField("graceUntil", flags).GetValue(logic) });
        }
        var fingers = new List<object>();
        foreach (var touch in touches) fingers.Add(new { id = touch.Id, phase = touch.Phase.ToString(),
            x = touch.VerticalPos.x * 100, y = touch.VerticalPos.y * 100 });
        var arcs = new List<object>();
        Camera camera = gameplay.Camera.GameplayCamera;
        foreach (var arc in diagnosticArcs)
            if (arc.Timing <= now && now <= arc.EndTiming + 500)
            {
                Vector3 screen = camera.WorldToScreenPoint(new Vector3(arc.WorldSegmentedXAt(now), arc.WorldSegmentedYAt(now), 0));
                arcs.Add(new { start = arc.Timing, end = arc.EndTiming, color = arc.Color,
                    x_start = arc.XStart, x_end = arc.XEnd, y_start = arc.YStart, y_end = arc.YEnd,
                    x = arc.WorldSegmentedXAt(now) * 100, y = arc.WorldSegmentedYAt(now) * 100,
                    screen_x = screen.x, screen_y = screen.y });
            }
        Vector3 position = camera.transform.position, rotation = camera.transform.eulerAngles;
        diagnosticFrames.Add(new { chart_ms = now, colors, fingers, arcs,
            screen_hitbox_x = Values.LaneScreenHitboxHorizontal * 2 * Values.ArcHitboxX / Values.LaneWidth,
            screen_hitbox_y = Values.LaneScreenHitboxVertical * 2 * Values.ArcHitboxY / Values.LaneWidth,
            camera_position = new[] { position.x, position.y, position.z },
            camera_rotation = new[] { rotation.x, rotation.y, rotation.z },
            field_of_view = camera.fieldOfView });
    }
    private class ArcReceiver : IArcJudgementReceiver
    {
        private readonly ArcJudgementRequest request;
        public ArcReceiver(ArcJudgementRequest value) { request = value; }
        public void ProcessArcJudgement(bool expired, bool judgement, GroupProperties props)
        {
            int now = gameplay.Audio.ChartTiming;
            var arc = request.Arc;
            string verdict = expired ? props.MapJudgementResult(JudgementResult.MissLate).ToString()
                : now <= arc.EndTiming + 240 ? props.MapJudgementResult(JudgementResult.Max).ToString() : "NoScore";
            result.arc_ticks.Add(new ArcTick { chart_ms = now, arc_start_ms = arc.Timing, arc_end_ms = arc.EndTiming,
                color = arc.Color, x_start = arc.XStart, x_end = arc.XEnd, y_start = arc.YStart, y_end = arc.YEnd,
                request_start_ms = request.StartAtTiming, auto_ms = request.AutoAtTiming, expiry_ms = request.ExpireAtTiming, result = verdict });
            request.Receiver.ProcessArcJudgement(expired, judgement, props);
        }
    }

    public static void Judged(JudgementResult value)
    {
        if (!Active) return;
        string name = value.ToString();
        if (name.StartsWith("Miss")) result.misses++; else result.hits++;
        result.judgements.Add(new Judge { chart_ms = gameplay.Audio.ChartTiming, result = name });
    }

    public static void Tap(int timing) { Hit("tap:" + timing, gameplay == null ? null : gameplay.Audio.TapHitsoundClip); }
    public static void Arc(int timing) { Hit("arc:" + timing, gameplay == null ? null : gameplay.Audio.ArcHitsoundClip); }
    public static void ArcTap(int timing, string sfx, bool fromJudgement)
    {
        if (!Active || !fromJudgement || string.IsNullOrEmpty(sfx) || sfx == "none") return;
        AudioClip clip;
        if (gameplay.Audio.SfxAudioClips.TryGetValue(sfx, out clip)) Hit("sfx:" + sfx + ":" + timing, clip);
    }
    private static void Hit(string key, AudioClip clip)
    {
        if (Active && clip != null && soundKeys.Add(key)) sounds.Add(new Sound { timing = gameplay.Audio.ChartTiming, clip = clip });
    }

    public static void Finish(string output, int from, int to)
    {
        if (!Enabled) return;
        var score = UnityEngine.Object.FindObjectOfType<ArcCreate.Gameplay.Score.ScoreService>(true);
        result.score = score.CurrentScore; result.combo = score.CurrentCombo;
        File.WriteAllText(output + ".judgements.json", JsonConvert.SerializeObject(result, Formatting.Indented));
        if (diagnosticFrames.Count > 0)
            File.WriteAllText(output + ".diagnostics.json", JsonConvert.SerializeObject(diagnosticFrames, Formatting.Indented));
        const int rate = 48000, channels = 2;
        float[] mixed = new float[Mathf.CeilToInt((to - from) / 1000f * rate) * channels];
        var cache = new Dictionary<AudioClip, float[]>();
        foreach (var sound in sounds)
        {
            float[] samples;
            if (!cache.TryGetValue(sound.clip, out samples)) { samples = new float[sound.clip.samples * sound.clip.channels]; sound.clip.GetData(samples, 0); cache[sound.clip] = samples; }
            int at = Mathf.RoundToInt((sound.timing + input.audio_offset_ms - from) / 1000f * rate);
            int frames = Mathf.CeilToInt(sound.clip.samples * (float)rate / sound.clip.frequency);
            for (int f = 0; f < frames; f++)
            {
                int destination = (at + f) * channels;
                if (destination < 0 || destination + 1 >= mixed.Length) continue;
                int source = Mathf.Min(sound.clip.samples - 1, f * sound.clip.frequency / rate) * sound.clip.channels;
                for (int c = 0; c < channels; c++) mixed[destination + c] += samples[source + Math.Min(c, sound.clip.channels - 1)] * .16f;
            }
        }
        using (var writer = new BinaryWriter(File.Create(output + ".hits.wav")))
        {
            writer.Write(System.Text.Encoding.ASCII.GetBytes("RIFF")); writer.Write(36 + mixed.Length * 2);
            writer.Write(System.Text.Encoding.ASCII.GetBytes("WAVEfmt ")); writer.Write(16); writer.Write((short)1);
            writer.Write((short)channels); writer.Write(rate); writer.Write(rate * channels * 2); writer.Write((short)(channels * 2)); writer.Write((short)16);
            writer.Write(System.Text.Encoding.ASCII.GetBytes("data")); writer.Write(mixed.Length * 2);
            foreach (float sample in mixed) writer.Write((short)(Mathf.Clamp(sample, -1, 1) * short.MaxValue));
        }
        Debug.Log("REPLAY RESULT: " + result.hits + " hits, " + result.misses + " misses");
        Enabled = false;
    }
}

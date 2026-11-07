// Compatibility for designant traces and their non-scoring sky taps.
// Touch requests, hitboxes, expiry, particles and sounds remain native ArcCreate.
using System;
using System.Linq;
using System.Reflection;
using ArcCreate.Gameplay.Chart;
using ArcCreate.Gameplay.Data;
using ArcCreate.Gameplay.Judgement;
using ArcCreate.Gameplay.Skin;
using ArcCreate.Utility.Extension;
using UnityEngine;

public static class RedNotes
{
    public const string Marker = "__rhythm_red_note__";
    public static bool SuppressScore;
    private static readonly BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    public static bool IsRed(Note note) { var tap = note as ArcTap; return tap != null && tap.Arc.Sfx == Marker; }
    public static bool IsRedArc(Arc arc) { return arc != null && arc.Sfx == Marker; }

    public static void Begin(ArcTap note) { SuppressScore = IsRed(note); }
    public static void End(ArcTap note, int offset, GroupProperties props)
    {
        if (IsRed(note)) Replay.RedJudged(note.Timing, props.MapJudgementResult(offset.CalculateJudgeResult()));
        SuppressScore = false;
    }
    public static bool SkipScore() { return SuppressScore; }
    public static int ComboAt(int original, TimingGroup group, int timing)
    {
        if (group.GroupProperties.NoInput) return original;
        return original - group.GetEventType<ArcTap>().Count(note => IsRed(note) && note.Timing <= timing);
    }
    public static Color TintTap(Color original, ArcTap note) { return IsRed(note) ? Red(original) : original; }
    public static Color TintArc(Color original, Arc note) { return IsRedArc(note) ? Red(original) : original; }
    private static Color Red(Color original) { return new Color(original.r, original.g * .04f, original.b * .04f, original.a); }

    public static void ReloadSkin(ArcTap note)
    {
        if (!IsRed(note)) return;
        // The marker identifies semantics, not a custom-SFX note mesh or sound.
        string sfx = note.Arc.Sfx;
        try
        {
            note.Arc.Sfx = "none";
            var skin = UnityEngine.Object.FindObjectOfType<SkinService>(true);
            typeof(ArcTap).GetField("texture", Private).SetValue(note, skin.GetArcTapSkin(note));
            typeof(ArcTap).GetField("isSfx", Private).SetValue(note, false);
        }
        finally { note.Arc.Sfx = sfx; }
    }
}

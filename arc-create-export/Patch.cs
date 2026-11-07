using System;
using System.IO;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

internal static class Patch
{
    private static void Main(string[] args)
    {
        string folder = args[0];
        string composePath = Path.Combine(folder, "ArcCreate.Compose.dll");
        using (var compose = AssemblyDefinition.ReadAssembly(composePath, new ReaderParameters { InMemory = true }))
        using (var export = AssemblyDefinition.ReadAssembly(Path.Combine(folder, "ArcCreateOfflineExport.dll")))
        {
            var startup = compose.MainModule.GetType("ArcCreate.Compose.ComposeManager")
                .Methods.Single(method => method.Name == "StartCheckingStartupArgs");
            var start = export.MainModule.GetType("Export").Methods.Single(method => method.Name == "Start");
            var call = compose.MainModule.ImportReference(start);
            var il = startup.Body.GetILProcessor();
            // Keep existing branches targeting the return valid by converting that instruction to the entry hook.
            foreach (var ret in startup.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToArray())
            {
                ret.OpCode = OpCodes.Ldarg_0;
                var invoke = il.Create(OpCodes.Call, call);
                il.InsertAfter(ret, invoke);
                il.InsertAfter(invoke, il.Create(OpCodes.Ret));
            }
            compose.Write(composePath + ".new");
        }
        File.Replace(composePath + ".new", composePath, null);
        string gameplayPath = Path.Combine(folder, "ArcCreate.Gameplay.dll");
        using (var gameplay = AssemblyDefinition.ReadAssembly(gameplayPath, new ReaderParameters { InMemory = true }))
        using (var export = AssemblyDefinition.ReadAssembly(Path.Combine(folder, "ArcCreateOfflineExport.dll")))
        {
            var replay = export.MainModule.GetType("Replay");
            Action<string, string, string, int> hook = (type, name, callback, count) => {
                var method = gameplay.MainModule.GetType(type).Methods.Single(m => m.Name == name);
                var call = gameplay.MainModule.ImportReference(replay.Methods.Single(m => m.Name == callback));
                var il = method.Body.GetILProcessor();
                var first = method.Body.Instructions.First();
                for (int i = 1; i <= count; i++) il.InsertBefore(first, il.Create(OpCodes.Ldarg, method.Parameters[i - 1]));
                il.InsertBefore(first, il.Create(OpCodes.Call, call));
            };
            hook("ArcCreate.Gameplay.Score.ScoreService", "ProcessJudgement", "Judged", 1);
            hook("ArcCreate.Gameplay.Hitsound.HitsoundService", "PlayTapHitsound", "Tap", 1);
            hook("ArcCreate.Gameplay.Hitsound.HitsoundService", "PlayArcHitsound", "Arc", 1);
            hook("ArcCreate.Gameplay.Hitsound.HitsoundService", "PlayArcTapHitsound", "ArcTap", 3);
            var request = gameplay.MainModule.GetType("ArcCreate.Gameplay.Judgement.JudgementService").Methods
                .Single(m => m.Name == "Request" && m.Parameters.Count == 1 && m.Parameters[0].ParameterType.FullName == "ArcCreate.Gameplay.Judgement.ArcJudgementRequest");
            var track = gameplay.MainModule.ImportReference(replay.Methods.Single(m => m.Name == "TrackArcRequest"));
            var requestIl = request.Body.GetILProcessor();
            var begin = request.Body.Instructions.First();
            requestIl.InsertBefore(begin, requestIl.Create(OpCodes.Ldarg_1));
            requestIl.InsertBefore(begin, requestIl.Create(OpCodes.Call, track));
            requestIl.InsertBefore(begin, requestIl.Create(OpCodes.Starg, request.Parameters[0]));
            PatchRedNotes(gameplay.MainModule, export.MainModule.GetType("RedNotes"));
            gameplay.Write(gameplayPath + ".new");
        }
        File.Replace(gameplayPath + ".new", gameplayPath, null);
    }

    private static void PatchRedNotes(ModuleDefinition module, TypeDefinition red)
    {
        Func<string, MethodReference> callback = name => module.ImportReference(red.Methods.Single(m => m.Name == name));
        Func<string, string, MethodDefinition> method = (type, name) => module.GetType(type).Methods.Single(m => m.Name == name);
        Action<MethodDefinition, string, bool, bool> guard = (target, hook, loadThis, zero) => {
            var il = target.Body.GetILProcessor(); var first = target.Body.Instructions.First();
            if (loadThis) il.InsertBefore(first, il.Create(OpCodes.Ldarg_0));
            il.InsertBefore(first, il.Create(OpCodes.Call, callback(hook)));
            il.InsertBefore(first, il.Create(OpCodes.Brfalse, first));
            if (zero) il.InsertBefore(first, il.Create(OpCodes.Ldc_I4_0));
            il.InsertBefore(first, il.Create(OpCodes.Ret));
        };
        guard(method("ArcCreate.Gameplay.Data.Note", "get_TotalCombo"), "IsRed", true, true);
        guard(method("ArcCreate.Gameplay.Score.ScoreService", "ProcessJudgement"), "SkipScore", false, false);

        var judge = method("ArcCreate.Gameplay.Data.ArcTap", "ProcessArcTapJudgement");
        var judgeIl = judge.Body.GetILProcessor(); var begin = judge.Body.Instructions.First();
        judgeIl.InsertBefore(begin, judgeIl.Create(OpCodes.Ldarg_0));
        judgeIl.InsertBefore(begin, judgeIl.Create(OpCodes.Call, callback("Begin")));
        foreach (var ret in judge.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToArray())
        {
            ret.OpCode = OpCodes.Ldarg_0;
            var a = judgeIl.Create(OpCodes.Ldarg_1); judgeIl.InsertAfter(ret, a);
            var b = judgeIl.Create(OpCodes.Ldarg_2); judgeIl.InsertAfter(a, b);
            var call = judgeIl.Create(OpCodes.Call, callback("End")); judgeIl.InsertAfter(b, call);
            judgeIl.InsertAfter(call, judgeIl.Create(OpCodes.Ret));
        }
        var combo = method("ArcCreate.Gameplay.Chart.TimingGroup", "ComboAt");
        var comboIl = combo.Body.GetILProcessor();
        foreach (var ret in combo.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToArray())
        {
            ret.OpCode = OpCodes.Ldarg_0;
            var a = comboIl.Create(OpCodes.Ldarg_1); comboIl.InsertAfter(ret, a);
            var call = comboIl.Create(OpCodes.Call, callback("ComboAt")); comboIl.InsertAfter(a, call);
            comboIl.InsertAfter(call, comboIl.Create(OpCodes.Ret));
        }
        foreach (string name in new[] { "ArcTap", "Arc" })
        {
            var render = method("ArcCreate.Gameplay.Data." + name, "UpdateRender");
            var il = render.Body.GetILProcessor();
            var colors = render.Body.Instructions.Where(i => i.Operand is MethodReference
                && ((MethodReference)i.Operand).FullName.Contains("GroupProperties::get_Color")).ToArray();
            if (colors.Length == 0) throw new Exception("Missing native color hook: " + name);
            foreach (var color in colors)
            {
                var load = il.Create(OpCodes.Ldarg_0); il.InsertAfter(color, load);
                il.InsertAfter(load, il.Create(OpCodes.Call, callback(name == "ArcTap" ? "TintTap" : "TintArc")));
            }
        }
        var skin = method("ArcCreate.Gameplay.Data.ArcTap", "ReloadSkin");
        var skinIl = skin.Body.GetILProcessor();
        foreach (var ret in skin.Body.Instructions.Where(i => i.OpCode == OpCodes.Ret).ToArray())
        {
            ret.OpCode = OpCodes.Ldarg_0;
            var call = skinIl.Create(OpCodes.Call, callback("ReloadSkin")); skinIl.InsertAfter(ret, call);
            skinIl.InsertAfter(call, skinIl.Create(OpCodes.Ret));
        }
    }
}

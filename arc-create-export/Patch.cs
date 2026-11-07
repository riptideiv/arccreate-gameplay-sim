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
            gameplay.Write(gameplayPath + ".new");
        }
        File.Replace(gameplayPath + ".new", gameplayPath, null);
    }
}

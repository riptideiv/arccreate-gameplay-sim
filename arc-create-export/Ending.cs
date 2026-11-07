// Render just a completion/results clip using the shipped Result scene assets.
using System;
using System.IO;
using System.Linq;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Reflection;
using Newtonsoft.Json;
using ArcCreate.Compose;
using ArcCreate.Data;
using ArcCreate.SceneTransition;
using Cysharp.Threading.Tasks;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
using TMPro;
using Debug = UnityEngine.Debug;

public static class Ending
{
    public class Config
    {
        public string output, ffmpeg, background, jacket, title, composer, difficulty, charter, color, clear_result;
        public PlayResult play;
        public int fps = 60;
        public float result_seconds = 6;
    }
    public static bool Start(ComposeManager manager)
    {
        var args = Environment.GetCommandLineArgs(); int i = Array.IndexOf(args, "--export-ending");
        if (i < 0) return false;
        // The editor popup handler cannot display missing mobile-controller
        // notices after its canvases are disabled; keep the normal file logger.
        var log=typeof(ComposeManager).GetMethod("OnLog",BindingFlags.Instance|BindingFlags.NonPublic);
        Application.logMessageReceived -= (Application.LogCallback)Delegate.CreateDelegate(typeof(Application.LogCallback),manager,log);
        manager.StartCoroutine(Guard(Run(JsonConvert.DeserializeObject<Config>(File.ReadAllText(args[i+1])))));
        return true;
    }
    static IEnumerator Guard(IEnumerator task)
    {
        while (true)
        {
            object step;
            try { if (!task.MoveNext()) yield break; step=task.Current; }
            catch(Exception e) { Debug.LogException(e); Application.Quit(1); yield break; }
            yield return step;
        }
    }
    static string PathOf(Transform t) { return t.parent == null ? t.name : PathOf(t.parent)+"/"+t.name; }
    static Texture2D Texture(string path)
    {
        var t=new Texture2D(2,2,TextureFormat.RGB24,false); if (!t.LoadImage(File.ReadAllBytes(path))) throw new Exception("Could not load ending image: "+path);return t;
    }
    static void SetText(GameObject[] roots,string path,string value)
    {
        var item=roots.SelectMany(r=>r.GetComponentsInChildren<TMP_Text>(true)).FirstOrDefault(t=>PathOf(t.transform)==path);
        if (item == null) throw new Exception("Native result text missing: "+path);
        item.text=value;
    }
    static void Hide(GameObject[] roots,string path)
    {
        foreach(var t in roots.SelectMany(r=>r.GetComponentsInChildren<Transform>(true))) if(PathOf(t)==path)t.gameObject.SetActive(false);
    }
    static IEnumerator Run(Config c)
    {
        if(c.fps!=60 || c.play==null || c.play.NoteCount<=0) throw new Exception("Invalid native ending configuration.");
        Application.runInBackground=true; Time.timeScale=1;
        // Check the saved ledger against the engine's real full-chart count.
        // Loading the chart does not simulate/replay any contacts.
        var services=typeof(ComposeManager).Assembly.GetType("ArcCreate.Compose.Services");
        var gameplay=(ArcCreate.Gameplay.IGameplayControl)services.GetProperty("Gameplay").GetValue(null);
        float deadline=Time.realtimeSinceStartup+15;
        while(!gameplay.IsLoaded && Time.realtimeSinceStartup<deadline)yield return null;
        if(!gameplay.IsLoaded)throw new Exception("Timed out loading the native chart for results validation.");
        yield return new WaitForSecondsRealtime(.6f);
        var scoreService=UnityEngine.Object.FindObjectOfType<ArcCreate.Gameplay.Score.ScoreService>(true);
        int nativeCount=scoreService.GetPlayResult().NoteCount;
        if(nativeCount!=c.play.NoteCount)throw new Exception("Saved judgements do not cover the native chart: "+c.play.NoteCount+" / "+nativeCount);
        Screen.SetResolution(1148,802,false);
        yield return new WaitForSecondsRealtime(.25f);
        var transition=TransitionScene.Instance;
        if(transition==null)throw new Exception("Native transition scene is unavailable.");
        var privateFields=BindingFlags.Instance|BindingFlags.NonPublic;
        var transitionCanvases=(Canvas[])typeof(TransitionScene).GetField("canvases",privateFields).GetValue(transition);
        var transitionCamera=(Camera)typeof(TransitionScene).GetField("transitionCamera",privateFields).GetValue(transition);
        foreach(var canvas in UnityEngine.Object.FindObjectsOfType<Canvas>(true))
            if(!transitionCanvases.Any(t=>t==canvas || t.transform.IsChildOf(canvas.transform)))canvas.gameObject.SetActive(false);
        foreach(var camera in UnityEngine.Object.FindObjectsOfType<Camera>(true))camera.enabled=false;
        // Undo the editor's Topmost gameplay-camera binding. The native results
        // flow keeps the triangle canvas as its background beneath Result's camera.
        transitionCamera.enabled=true;
        transition.SetTargetCamera(transitionCamera,"Default",0);
        foreach(var canvas in transitionCanvases)canvas.gameObject.SetActive(true);
        transition.PlayRetryCountGameObject.SetActive(false);transition.InfoGameObject.SetActive(false);
        var sequence=new TransitionSequence().OnShow().AddTransition(new TriangleTileTransition())
            .OnBoth().AddTransition(new DecorationTransition());
        sequence.DisableGameObject();
        // The editor boot leaves these tiles opaque. Normal gameplay has already
        // completed HideTriangleTile before its result transition begins.
        var gameplayTransition=transition.HideTriangleTile(false);
        while(gameplayTransition.Status==UniTaskStatus.Pending)yield return null;
        gameplayTransition.GetAwaiter().GetResult();
        var bgCam=new GameObject("Saved gameplay camera").AddComponent<Camera>();
        bgCam.depth=-100;bgCam.clearFlags=CameraClearFlags.SolidColor;bgCam.backgroundColor=Color.black;bgCam.cullingMask=1<<31;
        var backdrop=new GameObject("Saved gameplay",typeof(Canvas),typeof(CanvasScaler));backdrop.layer=31;
        var screen=backdrop.GetComponent<Canvas>();screen.renderMode=RenderMode.ScreenSpaceCamera;screen.worldCamera=bgCam;screen.planeDistance=1;
        var scaler=backdrop.GetComponent<CanvasScaler>();scaler.uiScaleMode=CanvasScaler.ScaleMode.ScaleWithScreenSize;scaler.referenceResolution=new Vector2(1148,802);
        var bg=new GameObject("Last native gameplay frame",typeof(RectTransform),typeof(RawImage));bg.layer=31;bg.transform.SetParent(backdrop.transform,false);
        var br=bg.GetComponent<RectTransform>();br.anchorMin=Vector2.zero;br.anchorMax=Vector2.one;br.offsetMin=br.offsetMax=Vector2.zero;bg.GetComponent<RawImage>().texture=Texture(c.background);
        var load=SceneManager.LoadSceneAsync("Result",LoadSceneMode.Additive);
        if(load==null)throw new Exception("The ArcCreate build does not contain its Result scene.");
        yield return load;
        var roots=SceneManager.GetSceneByName("Result").GetRootGameObjects();
        Fill(roots,c);
        foreach(var scale in roots.SelectMany(r=>r.GetComponentsInChildren<CanvasScaler>(true)))scale.matchWidthOrHeight=0;
        var resultAnimator=roots.Single(r=>r.name=="InfoCanvas").GetComponent<ArcCreate.Utility.Animation.ScriptedAnimator>();
        if(resultAnimator==null)throw new Exception("Native results entrance animator is missing.");
        resultAnimator.HideImmediate();
        foreach(var root in roots)root.SetActive(false);
        Canvas.ForceUpdateCanvases();
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(c.output));
        var process=Process.Start(new ProcessStartInfo(c.ffmpeg,"-hide_banner -loglevel error -f rawvideo -pixel_format rgb24 -video_size 1148x802 -framerate 60 -i - -an -c:v libx264 -preset veryfast -crf 17 -pix_fmt yuv420p -movflags +faststart -y \""+c.output+"\"") {UseShellExecute=false,RedirectStandardInput=true});
        Time.captureFramerate=c.fps;
        // The same Show -> load/display -> Hide sequence used by StorageData.
        // Capture the native tasks while Unity/DOTween advance at 60 Hz.
        var showing=sequence.Show();UniTask hiding=default(UniTask);
        int frames=0,resultStart=-1,settled=-1;float resultVolume=1;
        using(var writer=new BinaryWriter(process.StandardInput.BaseStream))
        {
            while(true)
            {
                if(frames>c.fps*30)throw new Exception("Native results transition did not complete.");
                if(resultStart<0 && showing.Status!=UniTaskStatus.Pending)
                {
                    showing.GetAwaiter().GetResult();resultStart=frames;
                    backdrop.SetActive(false);
                    foreach(var root in roots)root.SetActive(true);
                    // ResultScreen.StartDisplay plays this source then Show().
                    var audio=roots.SelectMany(r=>r.GetComponentsInChildren<AudioSource>(true)).FirstOrDefault(a=>a.clip!=null);
                    if(audio!=null){resultVolume=audio.volume;audio.Play();}
                    resultAnimator.Show();
                    Canvas.ForceUpdateCanvases();
                    hiding=sequence.Hide();
                }
                if(resultStart>=0 && settled<0 && hiding.Status!=UniTaskStatus.Pending
                    && frames-resultStart>=Mathf.CeilToInt(resultAnimator.Length*c.fps))
                {hiding.GetAwaiter().GetResult();settled=frames;}
                if(settled>=0 && frames-settled>=Mathf.RoundToInt(c.result_seconds*c.fps))break;
                yield return new WaitForEndOfFrame();
                var capture=ScreenCapture.CaptureScreenshotAsTexture();var pixels=capture.GetPixels32();int w=capture.width,h=capture.height;
                if(w!=1148||h!=802)throw new Exception("Unexpected ending capture dimensions: "+w+"x"+h);
                byte[] bytes=new byte[w*h*3];int k=0;
                for(int y=h-1;y>=0;y--)for(int x=0;x<w;x++){var pixel=pixels[y*w+x];bytes[k++]=pixel.r;bytes[k++]=pixel.g;bytes[k++]=pixel.b;}
                writer.Write(bytes);UnityEngine.Object.Destroy(capture);frames++;
            }
        }
        process.WaitForExit();if(process.ExitCode!=0)throw new Exception("Ending video encoder failed.");
        Time.captureFramerate=0;
        File.WriteAllText(c.output+".result.json",JsonConvert.SerializeObject(new {flow="arccreate-native-v1",clear_result=c.play.ClearResult.ToString(),play=c.play,frames,fps=c.fps,duration_s=(double)frames/c.fps,results_start_s=(double)resultStart/c.fps,settled_s=(double)settled/c.fps,result_music_volume=resultVolume,native_scene="Result",show_ms=sequence.ShowDurationMs,hide_ms=sequence.HideDurationMs},Formatting.Indented));
        Debug.Log("ENDING export complete: "+c.output);Application.Quit(0);
    }
    static void Fill(GameObject[] roots,Config c)
    {
        Action<string,string> set=(path,value)=>SetText(roots,"InfoCanvas/"+path,value);
        set("Jacket/Title",c.title);set("Jacket/Composer",c.composer);set("Jacket/JacketFrame/DifficultyFrame/DifficultyText",c.difficulty);
        set("CharterFrame/Charter/Text",c.charter ?? ""); Hide(roots,"InfoCanvas/CharterFrame/Alias");
        set("ScoreFrame/Score",PlayResult.FormatScore(c.play.Score));Hide(roots,"InfoCanvas/ScoreFrame/Best");Hide(roots,"InfoCanvas/ScoreFrame/BestLabel");Hide(roots,"InfoCanvas/ScoreFrame/Upscore");
        string table="JudgementFrame/JudgementTable/";
        set(table+"Combo/Count",c.play.MaxCombo.ToString());
        set(table+"Table/PerfectRow/Early",c.play.EarlyPerfectCount.ToString());set(table+"Table/PerfectRow/Total",c.play.PerfectCount.ToString());set(table+"Table/PerfectRow/Late",c.play.LatePerfectCount.ToString());
        set(table+"Table/GoodRow/Early",c.play.EarlyGoodCount.ToString());set(table+"Table/GoodRow/Total",c.play.GoodCount.ToString());set(table+"Table/GoodRow/Late",c.play.LateGoodCount.ToString());
        set(table+"Table/MissRow/Early",c.play.EarlyMissCount.ToString());set(table+"Table/MissRow/Total",c.play.MissCount.ToString());set(table+"Table/MissRow/Late",c.play.LateMissCount.ToString());
        Hide(roots,"InfoCanvas/"+table+"OffsetInfo");Hide(roots,"InfoCanvas/"+table+"PlayRetryTable");Hide(roots,"InfoCanvas/"+table+"AutoOn");Hide(roots,"InfoCanvas/CharacterParent");
        foreach(var raw in roots.SelectMany(r=>r.GetComponentsInChildren<RawImage>(true))) if(PathOf(raw.transform)=="InfoCanvas/Jacket/Jacket")raw.texture=Texture(c.jacket);
        foreach(var display in roots.SelectMany(r=>r.GetComponentsInChildren<ArcCreate.Selection.Interface.ClearResultDisplay>(true)))display.Display(c.play.ClearResult);
        foreach(var display in roots.SelectMany(r=>r.GetComponentsInChildren<ArcCreate.Selection.Interface.GradeDisplay>(true)))display.Display(c.play.Grade);
        Color color; if(ColorUtility.TryParseHtmlString(c.color,out color))foreach(var image in roots.SelectMany(r=>r.GetComponentsInChildren<Image>(true)))if(PathOf(image.transform)=="InfoCanvas/Jacket/JacketFrame/DifficultyFrame")image.color=color;
        var dump=roots.SelectMany(r=>r.GetComponentsInChildren<Transform>(true)).Select(t=>PathOf(t)).ToArray();File.WriteAllText(c.output+".hierarchy.json",JsonConvert.SerializeObject(dump,Formatting.Indented));
    }
}

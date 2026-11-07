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
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
using TMPro;
using Debug = UnityEngine.Debug;

public static class Ending
{
    public class Config
    {
        public string output, ffmpeg, background, jacket, title, composer, difficulty, charter, color, banner;
        public PlayResult play;
        public int fps = 60;
        public float banner_seconds = 2.2f, result_seconds = 6;
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
        var font=Resources.FindObjectsOfTypeAll<TMP_FontAsset>().FirstOrDefault(f=>f.name.Contains("Exo")) ?? TMP_Settings.defaultFontAsset;
        foreach(var canvas in UnityEngine.Object.FindObjectsOfType<Canvas>(true)) canvas.gameObject.SetActive(false);
        foreach(var camera in UnityEngine.Object.FindObjectsOfType<Camera>(true)) camera.enabled=false;
        var bgCam=new GameObject("Ending background camera").AddComponent<Camera>(); bgCam.clearFlags=CameraClearFlags.SolidColor;bgCam.backgroundColor=Color.black;bgCam.cullingMask=0;
        var overlay=new GameObject("Completion",typeof(Canvas),typeof(CanvasScaler));var screen=overlay.GetComponent<Canvas>();screen.renderMode=RenderMode.ScreenSpaceOverlay;screen.sortingOrder=1000;
        var scaler=overlay.GetComponent<CanvasScaler>();scaler.uiScaleMode=CanvasScaler.ScaleMode.ScaleWithScreenSize;scaler.referenceResolution=new Vector2(1148,802);
        var bg=new GameObject("Last native gameplay frame",typeof(RectTransform),typeof(RawImage));bg.transform.SetParent(overlay.transform,false);
        var br=bg.GetComponent<RectTransform>();br.anchorMin=Vector2.zero;br.anchorMax=Vector2.one;br.offsetMin=br.offsetMax=Vector2.zero;bg.GetComponent<RawImage>().texture=Texture(c.background);
        var shade=new GameObject("Dim",typeof(RectTransform),typeof(Image));shade.transform.SetParent(overlay.transform,false);var sr=shade.GetComponent<RectTransform>();sr.anchorMin=Vector2.zero;sr.anchorMax=Vector2.one;sr.offsetMin=sr.offsetMax=Vector2.zero;shade.GetComponent<Image>().color=new Color(.08f,.04f,.14f,.38f);
        var label=new GameObject("Clear banner",typeof(RectTransform),typeof(TextMeshProUGUI));label.transform.SetParent(overlay.transform,false);var lr=label.GetComponent<RectTransform>();lr.anchorMin=lr.anchorMax=new Vector2(.5f,.5f);lr.sizeDelta=new Vector2(1100,140);
        var text=label.GetComponent<TextMeshProUGUI>();text.font=font;text.text=c.banner;text.fontSize=82;text.fontStyle=FontStyles.Italic|FontStyles.Bold;text.alignment=TextAlignmentOptions.Center;text.color=new Color(.95f,.89f,1);
        Canvas.ForceUpdateCanvases();
        // Load the native results scene additively. Its controller is excluded on
        // desktop, but its layout, textures, fonts and utility displays remain.
        var load=SceneManager.LoadSceneAsync("Result",LoadSceneMode.Additive);
        if(load==null)throw new Exception("The ArcCreate build does not contain its Result scene.");
        yield return load;
        var scene=SceneManager.GetSceneByName("Result");var roots=scene.GetRootGameObjects();
        foreach(var root in roots) root.SetActive(false);
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(c.output));
        var process=Process.Start(new ProcessStartInfo(c.ffmpeg,"-hide_banner -loglevel error -f rawvideo -pixel_format rgb24 -video_size 1148x802 -framerate 60 -i - -an -c:v libx264 -preset veryfast -crf 17 -pix_fmt yuv420p -movflags +faststart -y \""+c.output+"\"") {UseShellExecute=false,RedirectStandardInput=true});
        Time.captureFramerate=c.fps;
        int bannerFrames=Mathf.RoundToInt(c.banner_seconds*c.fps), frames=bannerFrames+Mathf.RoundToInt(c.result_seconds*c.fps);
        using(var writer=new BinaryWriter(process.StandardInput.BaseStream))
        {
            for(int frame=0;frame<frames;frame++)
            {
                if(frame==bannerFrames)
                {
                    overlay.SetActive(false);
                    foreach(var root in roots)root.SetActive(true);
                    Fill(roots,c);
                    foreach(var group in roots.SelectMany(r=>r.GetComponentsInChildren<CanvasGroup>(true)))group.alpha=1;
                    foreach(var canvas in roots.SelectMany(r=>r.GetComponentsInChildren<Canvas>(true)))canvas.sortingOrder+=100;
                    foreach(var scale in roots.SelectMany(r=>r.GetComponentsInChildren<CanvasScaler>(true)))scale.matchWidthOrHeight=0;
                    foreach(var animator in roots.SelectMany(r=>r.GetComponentsInChildren<ArcCreate.Utility.Animation.ScriptedAnimator>(true))) animator.ShowImmediate();
                    Canvas.ForceUpdateCanvases();
                }
                // Animate the title's arrival without changing any game judgements.
                if(frame<bannerFrames){float a=Mathf.Clamp01(frame/15f);text.alpha=a;lr.localScale=Vector3.one*(1.035f-.035f*a);}
                yield return new WaitForEndOfFrame();
                var capture=ScreenCapture.CaptureScreenshotAsTexture();var pixels=capture.GetPixels32();int w=capture.width,h=capture.height;
                if(w!=1148||h!=802)throw new Exception("Unexpected ending capture dimensions: "+w+"x"+h);
                byte[] bytes=new byte[w*h*3];int k=0;
                for(int y=h-1;y>=0;y--)for(int x=0;x<w;x++){var pixel=pixels[y*w+x];bytes[k++]=pixel.r;bytes[k++]=pixel.g;bytes[k++]=pixel.b;}
                writer.Write(bytes);UnityEngine.Object.Destroy(capture);
            }
        }
        process.WaitForExit();if(process.ExitCode!=0)throw new Exception("Ending video encoder failed.");
        Time.captureFramerate=0;
        File.WriteAllText(c.output+".result.json",JsonConvert.SerializeObject(new {banner=c.banner,play=c.play,frames,fps=c.fps,native_scene="Result"},Formatting.Indented));
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
        Hide(roots,"InfoCanvas/"+table+"OffsetInfo");Hide(roots,"InfoCanvas/"+table+"PlayRetryTable");Hide(roots,"InfoCanvas/"+table+"AutoOn");Hide(roots,"InfoCanvas/CharacterParent");Hide(roots,"ButtonCanvas");
        foreach(var raw in roots.SelectMany(r=>r.GetComponentsInChildren<RawImage>(true))) if(PathOf(raw.transform)=="InfoCanvas/Jacket/Jacket")raw.texture=Texture(c.jacket);
        foreach(var display in roots.SelectMany(r=>r.GetComponentsInChildren<ArcCreate.Selection.Interface.ClearResultDisplay>(true)))display.Display(c.play.ClearResult);
        foreach(var display in roots.SelectMany(r=>r.GetComponentsInChildren<ArcCreate.Selection.Interface.GradeDisplay>(true)))display.Display(c.play.Grade);
        Color color; if(ColorUtility.TryParseHtmlString(c.color,out color))foreach(var image in roots.SelectMany(r=>r.GetComponentsInChildren<Image>(true)))if(PathOf(image.transform)=="InfoCanvas/Jacket/JacketFrame/DifficultyFrame")image.color=color;
        var dump=roots.SelectMany(r=>r.GetComponentsInChildren<Transform>(true)).Select(t=>PathOf(t)).ToArray();File.WriteAllText(c.output+".hierarchy.json",JsonConvert.SerializeObject(dump,Formatting.Indented));
    }
}

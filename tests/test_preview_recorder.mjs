import assert from 'node:assert/strict';
import {PreviewRecorder, recordingFormat} from '../dashboard/preview-recorder.js';
let sources=0, trackId=0;
class Track { constructor(kind){this.kind=kind;this.id=++trackId;this.stopped=false;} clone(){return new Track(this.kind);} stop(){this.stopped=true;} }
class Stream {constructor(tracks=[]){this.tracks=tracks;}getTracks(){return this.tracks;}getVideoTracks(){return this.tracks.filter(t=>t.kind==='video');}getAudioTracks(){return this.tracks.filter(t=>t.kind==='audio');}}
class Audio {constructor(){this.destination={};}createMediaElementSource(){sources++;return {connect(){}};}createMediaStreamDestination(){return {stream:new Stream([new Track('audio')])};}async resume(){}}
class Recorder extends EventTarget {
 static isTypeSupported(t){return t.startsWith('video/mp4');}
 constructor(stream,opts){super();this.stream=stream;this.mimeType=opts.mimeType;this.state='inactive';Recorder.last=this;}
 start(){this.state='recording';}
 stop(){if(this.state==='inactive')return;this.state='inactive';const e=new Event('dataavailable');e.data=new Blob(['recorded audio and frames']);this.dispatchEvent(e);this.dispatchEvent(new Event('stop'));}
}
class Video extends EventTarget {
 constructor(){super();Object.assign(this,{time:1.25,playbackRate:.5,paused:true,muted:true,volume:.2,duration:3,readyState:4});}
 get currentTime(){return this.time;}set currentTime(t){this.time=t;queueMicrotask(()=>this.dispatchEvent(new Event('seeked')));}
 pause(){this.paused=true;}
 async play(){this.paused=false;this.captured={rate:this.playbackRate,muted:this.muted,volume:this.volume};if(!this.neverEnd)setTimeout(()=>{this.time=3;this.ended=true;this.paused=true;this.dispatchEvent(new Event('ended'));},5);}
}
Object.assign(globalThis,{MediaRecorder:Recorder,MediaStream:Stream,AudioContext:Audio,document:new EventTarget()});
const video=new Video(),states=[];
const preview={enabled:true,canvas:{width:640,height:480,captureStream:()=>new Stream([new Track('video')])},beginCapture(){this.captureLock=true;},renderAt(){},resize(){}};
const recording=new PreviewRecorder(preview,video,s=>states.push(s));
assert.equal(recordingFormat(),'video/mp4;codecs=avc1.42E01E,mp4a.40.2');
let result=await recording.record();
assert.equal(result.extension,'mp4');assert(result.blob.size>0);assert.equal(Recorder.last.stream.getTracks().length,2);
assert.deepEqual(video.captured,{rate:1,muted:false,volume:1});
assert.equal(video.currentTime,1.25);assert.equal(video.playbackRate,.5);assert.equal(video.volume,.2);assert(video.muted&&video.paused);assert(!recording.active&&!preview.captureLock);
assert(Recorder.last.stream.getTracks().every(t=>t.stopped));
await recording.record();assert.equal(sources,1,'Repeated exports must reuse the audio graph');
video.neverEnd=true;video.ended=false;
const pending=recording.record();setTimeout(()=>recording.cancel(),2);await assert.rejects(pending,/Export cancelled/);
assert(!recording.active&&!preview.captureLock);assert(video.paused&&video.muted);assert.equal(video.playbackRate,.5);
assert(!recording.audio.destination.stream.getAudioTracks()[0].stopped,'Only cloned recording audio tracks stop');
Recorder.isTypeSupported=()=>false;assert.equal(recordingFormat(),null);
console.log('Preview recorder: success, audio graph reuse, state restoration and cancellation passed.');

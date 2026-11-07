import fs from 'node:fs';
import assert from 'node:assert/strict';
import * as THREE from '../dashboard/vendor/three.module.js';
import { RhythmPreview } from '../dashboard/live-preview.js';

const folder = process.argv[2];
const motion = JSON.parse(fs.readFileSync(folder + '/motion.json'));
const reference = JSON.parse(fs.readFileSync(folder + '/alignment-reference.json'));
const preview = Object.create(RhythmPreview.prototype);
Object.assign(preview, {motion, canvas:{dataset:{}}, camera:new THREE.PerspectiveCamera(48,4/3,.01,100),
  nativeEuler:new THREE.Euler(), nativeQ:new THREE.Quaternion(),
  roots:{left:new THREE.Object3D(),right:new THREE.Object3D()}});
preview.setCamera(reference.settings);
preview.camera.updateMatrixWorld(true);
let positionError = 0, angularError = 0, projectionError = 0;
for (const check of reference.checks) {
  preview.pose(check.time);
  for (const side of ['left','right']) {
    const root = preview.roots[side], expected = check.hands[side];
    positionError = Math.max(positionError, root.position.distanceTo(new THREE.Vector3(...expected.position)));
    angularError = Math.max(angularError, root.quaternion.angleTo(new THREE.Quaternion(...expected.quaternion).normalize()));
    const ndc = root.position.clone().project(preview.camera);
    projectionError = Math.max(projectionError, Math.abs((ndc.x+1)/2-expected.screen[0]), Math.abs((ndc.y+1)/2-expected.screen[1]));
  }
}
assert(positionError < 1e-6, 'WebGL position differs from baked Blender');
assert(angularError < 1e-5, 'WebGL rotation differs from baked Blender');
assert(projectionError < 1e-5, 'WebGL camera projection differs from Blender');
const result = {verified:true, checks:reference.checks.length, maximum_position_error_m:positionError,
  maximum_rotation_error_rad:angularError, maximum_projection_error:projectionError};
fs.writeFileSync(folder + '/alignment-check.json',JSON.stringify(result,null,2));
console.log(result);

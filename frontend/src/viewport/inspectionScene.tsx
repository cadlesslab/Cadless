/** In-Canvas inspection: section clipping, measure annotations, and
 * the live triangle count. Picking is wired on the model group in Viewport. */
import { Html, Line } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import { type RefObject, useEffect } from "react";
import * as THREE from "three";

import { countTriangles, measureAngle, measureDistance, sectionPlane } from "./inspection";
import { useViewport, viewportStore, type Point3 } from "./viewportStore";

function boxOf(group: THREE.Object3D | null): THREE.Box3 | null {
  if (!group || group.children.length === 0) return null;
  const box = new THREE.Box3().setFromObject(group);
  return box.isEmpty() ? null : box;
}

/** Count triangles of the loaded model into the store; clear on unmount. */
export function TriangleCounter({ modelRef }: { modelRef: RefObject<THREE.Group> }) {
  useEffect(() => {
    viewportStore.setTriangleCount(countTriangles(modelRef.current));
    return () => viewportStore.setTriangleCount(null);
  }, [modelRef]);
  return null;
}

/** Apply / clear a section clipping plane on the model's materials. */
export function SectionPlane({ modelRef }: { modelRef: RefObject<THREE.Group> }) {
  const tool = useViewport((s) => s.tool);
  const axis = useViewport((s) => s.sectionAxis);
  const offset = useViewport((s) => s.sectionOffset);
  const gl = useThree((s) => s.gl);

  useEffect(() => {
    gl.localClippingEnabled = true;
    const apply = (planes: THREE.Plane[]) => {
      modelRef.current?.traverse((node) => {
        const mesh = node as THREE.Mesh;
        if (!mesh.isMesh) return;
        const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        for (const m of mats) {
          m.clippingPlanes = planes;
          m.needsUpdate = true;
        }
      });
    };
    if (tool !== "section") {
      apply([]);
      return;
    }
    const box = boxOf(modelRef.current);
    if (!box) return;
    const { normal, constant } = sectionPlane(axis, offset, box);
    apply([new THREE.Plane(new THREE.Vector3(...normal), constant)]);
    return () => apply([]);
  }, [tool, axis, offset, gl, modelRef]);

  return null;
}

function mid(a: Point3, b: Point3): Point3 {
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2];
}

/** Markers, segments + distance labels (and an angle label for 3 points). */
export function Measure() {
  const tool = useViewport((s) => s.tool);
  const pts = useViewport((s) => s.measurePoints);
  if (tool !== "measure" || pts.length === 0) return null;

  const segments: [Point3, Point3][] = [];
  for (let i = 0; i + 1 < pts.length; i++) segments.push([pts[i], pts[i + 1]]);

  return (
    <group>
      {pts.map((p, i) => (
        <mesh key={i} position={p}>
          <sphereGeometry args={[0.7, 12, 12]} />
          <meshBasicMaterial color="#4f8cff" depthTest={false} />
        </mesh>
      ))}
      {segments.map((s, i) => (
        <group key={`seg-${i}`}>
          <Line points={[s[0], s[1]]} color="#4f8cff" lineWidth={2} />
          <Html position={mid(s[0], s[1])} center>
            <span className="vp-measure-label">{measureDistance(s[0], s[1]).toFixed(1)} mm</span>
          </Html>
        </group>
      ))}
      {pts.length >= 3 && (
        <Html position={pts[1]} center>
          <span className="vp-measure-label">
            {measureAngle(pts[0], pts[1], pts[2]).toFixed(1)}°
          </span>
        </Html>
      )}
    </group>
  );
}

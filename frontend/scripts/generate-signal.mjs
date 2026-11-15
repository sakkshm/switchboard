import { mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// Original, deterministic one-bit artwork. A shaded trefoil tube is rasterized
// to a depth buffer, then Floyd–Steinberg dithering turns light into pixels.
const size = 320
const depth = new Float64Array(size * size).fill(-Infinity)
const light = new Float64Array(size * size)
const mask = new Uint8Array(size * size)
const normalize = v => { const n = Math.hypot(...v); return v.map(x => x / n) }
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
const rotate = ([x, y, z]) => {
  const a = .78, b = -.3, c = -.32
  const y1 = y*Math.cos(a)-z*Math.sin(a), z1 = y*Math.sin(a)+z*Math.cos(a)
  const x2 = x*Math.cos(b)+z1*Math.sin(b), z2 = -x*Math.sin(b)+z1*Math.cos(b)
  return [x2*Math.cos(c)-y1*Math.sin(c), x2*Math.sin(c)+y1*Math.cos(c), z2]
}
const point = t => [(2+.65*Math.cos(3*t))*Math.cos(2*t), (2+.65*Math.cos(3*t))*Math.sin(2*t), .85*Math.sin(3*t)]
const sun = normalize([-.6, -.7, 1])
for (let i = 0; i < 2600; i++) {
  const t = i / 2600 * Math.PI * 2
  const p = point(t), q = point(t+.0001)
  const tangent = normalize(q.map((v, k) => v-p[k]))
  const n = normalize(cross(tangent, [0, 0, 1]))
  const b = cross(tangent, n)
  for (let j = 0; j < 200; j++) {
    const angle = j / 200 * Math.PI * 2
    const normal = n.map((v, k) => v*Math.cos(angle)+b[k]*Math.sin(angle))
    const [x, y, z] = rotate(p.map((v, k) => v+.46*normal[k]))
    const rotatedNormal = rotate(normal)
    const px = Math.round(size/2+x*46), py = Math.round(size/2+y*46)
    const diffuse = Math.max(0, rotatedNormal.reduce((sum, v, k) => sum+v*sun[k], 0))
    const specular = Math.max(0, rotatedNormal[2]) ** 22
    const shade = Math.min(.96, .075 + diffuse*.78 + specular*.16)
    for (let dy = 0; dy <= 1; dy++) for (let dx = 0; dx <= 1; dx++) {
      const xx = px+dx, yy = py+dy, index = yy*size+xx
      if (xx < 0 || xx >= size || yy < 0 || yy >= size || z <= depth[index]) continue
      depth[index] = z; light[index] = shade; mask[index] = 1
    }
  }
}
let path = ''
for (let y = 0; y < size; y++) {
  for (let x = 0; x < size; x++) {
    const i = y*size+x
    if (!mask[i]) continue
    const value = light[i] >= .5 ? 1 : 0
    if (value) path += `M${x} ${y}h1v1h-1z`
    const error = light[i]-value
    for (const [dx, dy, weight] of [[1,0,7/16],[-1,1,3/16],[0,1,5/16],[1,1,1/16]]) {
      const xx = x+dx, yy = y+dy, index = yy*size+xx
      if (xx >= 0 && xx < size && yy < size && mask[index]) light[index] += error*weight
    }
  }
}
const directory = fileURLToPath(new URL('../public/', import.meta.url))
mkdirSync(directory, { recursive: true })
writeFileSync(`${directory}/signal-knot.svg`, `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" shape-rendering="crispEdges"><path fill="#f1f0e9" d="${path}"/></svg>\n`)
console.log('Generated original dithered signal artwork.')

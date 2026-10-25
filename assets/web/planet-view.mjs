import * as THREE from './vendor/three.module.min.js';

export function strikeLocation(planetId, strike) {
  const hasSlot = Number.isInteger(strike.slot) && strike.slot >= 0;
  let hash = 2166136261;
  for (const character of `${planetId}|${strike.pool || strike.aar_mode}|${hasSlot ? '' : strike.id}`) {
    hash = Math.imul(hash ^ character.charCodeAt(0), 16777619) >>> 0;
  }
  const index = hasSlot ? strike.slot : hash % 8;
  return {
    latitude: (index % 4 - 1.5) * .36 + ((hash >>> 16) / 65535 - .5) * .1,
    longitude: ((hash % 360) * Math.PI / 180 + index * 2.399963229728653) % (Math.PI * 2),
  };
}

export async function createPlanetView(host, {planet, artwork, strikes = [], onSelect}) {
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({alpha: true, antialias: true, powerPreference: 'low-power'});
  } catch {
    host.dataset.renderer = 'fallback';
    return {update() {}, focus() {}, dispose() {}};
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.className = 'planet-globe-canvas';
  renderer.domElement.setAttribute('aria-label', `${planet.name} deployment globe`);
  renderer.domElement.tabIndex = 0;
  host.append(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, .1, 50);
  camera.position.set(0, 0, 3.7);
  const globe = new THREE.Group();
  scene.add(globe);
  const geometry = new THREE.SphereGeometry(1, 72, 48);
  const material = new THREE.ShaderMaterial({
    uniforms: {portrait: {value: null}},
    vertexShader: 'varying vec3 surface; void main(){surface=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
    fragmentShader: `uniform sampler2D portrait; varying vec3 surface;
      void main(){vec3 normal=normalize(surface);
      vec4 front=texture2D(portrait,normal.xy*.49+.5);
      vec4 flank=texture2D(portrait,vec2(normal.z,normal.y)*.46+.5);
      vec4 pole=texture2D(portrait,vec2(normal.x,normal.z)*.46+.5);
      vec3 weights=pow(abs(normal),vec3(6.0));
      weights/=max(weights.x+weights.y+weights.z,.0001);
      vec4 blended=flank*weights.x+pole*weights.y+front*weights.z;
      vec4 authored=mix(blended,front,smoothstep(.38,.76,abs(normal.z)));
      gl_FragColor=vec4(authored.rgb,1.0);
      #include <colorspace_fragment>
      }`,
  });
  const sphere = new THREE.Mesh(geometry, material);
  globe.add(sphere);
  const atmosphereGeometry = new THREE.SphereGeometry(1.025, 48, 32);
  const atmosphereMaterial = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.BackSide,
    vertexShader: 'varying vec3 facing; void main(){facing=normalize(normalMatrix*normal); gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
    fragmentShader: 'varying vec3 facing; void main(){float rim=pow(1.0-abs(facing.z),3.0);gl_FragColor=vec4(.28,.65,.57,rim*.38);}',
  });
  globe.add(new THREE.Mesh(atmosphereGeometry, atmosphereMaterial));
  const markerGeometry = new THREE.SphereGeometry(.022, 12, 8);
  const ringGeometry = new THREE.RingGeometry(.04, .047, 32);
  const markers = new Map();
  let targetYaw = 0, targetPitch = 0, frame = 0, disposed = false, loaded = false;
  let dragging = false, lastPoint = null, moved = 0, lastFrame = 0;
  let selectedId = '';
  const vector = location => new THREE.Vector3(Math.cos(location.latitude) * Math.sin(location.longitude), Math.sin(location.latitude), Math.cos(location.latitude) * Math.cos(location.longitude));
  const resize = () => {
    if (disposed) return;
    const width = Math.max(1, host.clientWidth), height = Math.max(1, host.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.position.z = camera.aspect < 1 ? 3.7 / Math.max(.65, camera.aspect) : 3.7;
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();
  function update(offers) {
    const identities = new Set(offers.map(offer => offer.id));
    for (const [identity, marker] of markers) {
      if (!identities.has(identity)) {
        globe.remove(marker);
        marker.children.forEach(child => child.material.dispose());
        markers.delete(identity);
      }
    }
    for (const offer of offers) {
      if (markers.has(offer.id)) continue;
      const location = strikeLocation(planet.id, offer);
      const position = vector(location);
      const marker = new THREE.Group();
      marker.position.copy(position.clone().multiplyScalar(1.014));
      marker.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), position);
      marker.userData = {id: offer.id, location};
      marker.add(new THREE.Mesh(markerGeometry, new THREE.MeshBasicMaterial({color: 0xf2c96c})));
      marker.add(new THREE.Mesh(ringGeometry, new THREE.MeshBasicMaterial({color: 0xf2c96c, side: THREE.DoubleSide})));
      globe.add(marker);
      markers.set(offer.id, marker);
    }
    host.dataset.markers = String(markers.size);
    if (selectedId && !markers.has(selectedId)) selectedId = '';
  }
  function focus(identity) {
    const marker = markers.get(identity);
    if (!marker) return;
    selectedId = identity;
    targetYaw = -marker.userData.location.longitude;
    targetPitch = marker.userData.location.latitude;
    while (targetYaw - globe.rotation.y > Math.PI) targetYaw -= Math.PI * 2;
    while (targetYaw - globe.rotation.y < -Math.PI) targetYaw += Math.PI * 2;
    for (const [key, item] of markers) {
      item.scale.setScalar(key === identity ? 1.5 : 1);
      item.children.forEach(child => child.material.color.setHex(key === identity ? 0xffffff : 0xf2c96c));
    }
    host.dataset.focusedStrike = identity;
    if (reducedMotion.matches) globe.rotation.set(targetPitch, targetYaw, 0, 'XYZ');
  }
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  function pick(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects([sphere, ...markers.values()], true);
    const hit = hits.find(entry => entry.object !== sphere);
    if (!hit || hits[0].object === sphere) return;
    const marker = hit.object.parent;
    focus(marker.userData.id);
    onSelect?.(marker.userData.id);
  }
  function down(event) {
    event.stopPropagation();
    dragging = true; moved = 0;
    lastPoint = {x: event.clientX, y: event.clientY};
    renderer.domElement.setPointerCapture(event.pointerId);
  }
  function move(event) {
    event.stopPropagation();
    if (!dragging) return;
    const dx = event.clientX - lastPoint.x, dy = event.clientY - lastPoint.y;
    moved += Math.abs(dx) + Math.abs(dy);
    targetYaw += dx * .007;
    targetPitch = THREE.MathUtils.clamp(targetPitch + dy * .005, -1.1, 1.1);
    lastPoint = {x: event.clientX, y: event.clientY};
    if (reducedMotion.matches) globe.rotation.set(targetPitch, targetYaw, 0, 'XYZ');
  }
  function up(event) {
    event.stopPropagation();
    if (!dragging) return;
    dragging = false;
    if (event.type === 'pointerup' && moved < 7) pick(event);
  }
  function keyboard(event) {
    const keys = {ArrowLeft: [-.18, 0], ArrowRight: [.18, 0], ArrowUp: [0, -.12], ArrowDown: [0, .12]};
    if (!keys[event.key]) return;
    event.preventDefault();
    targetYaw += keys[event.key][0];
    targetPitch = THREE.MathUtils.clamp(targetPitch + keys[event.key][1], -1.1, 1.1);
    if (reducedMotion.matches) globe.rotation.set(targetPitch, targetYaw, 0, 'XYZ');
  }
  const events = {pointerdown: down, pointermove: move, pointerup: up, pointercancel: up, keydown: keyboard};
  Object.entries(events).forEach(([name, handler]) => renderer.domElement.addEventListener(name, handler));
  function lost(event) {
    event.preventDefault(); host.dataset.renderer = 'fallback';
    dispose();
  }
  renderer.domElement.addEventListener('webglcontextlost', lost);
  function dispose() {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(frame); observer.disconnect();
    Object.entries(events).forEach(([name, handler]) => renderer.domElement.removeEventListener(name, handler));
    renderer.domElement.removeEventListener('webglcontextlost', lost);
    material.uniforms.portrait.value?.dispose(); material.dispose(); geometry.dispose();
    atmosphereGeometry.dispose(); atmosphereMaterial.dispose(); markerGeometry.dispose(); ringGeometry.dispose();
    markers.forEach(marker => marker.children.forEach(child => child.material.dispose()));
    renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove();
  }
  function draw(timestamp) {
    if (!host.isConnected) { dispose(); return; }
    if (disposed) return;
    frame = requestAnimationFrame(draw);
    if (document.hidden || timestamp - lastFrame < 1000 / 30) return;
    lastFrame = timestamp;
    if (!reducedMotion.matches) {
      globe.rotation.order = 'XYZ';
      globe.rotation.y += (targetYaw - globe.rotation.y) * .14;
      globe.rotation.x += (targetPitch - globe.rotation.x) * .14;
    }
    if (loaded) renderer.render(scene, camera);
    host.dataset.yaw = globe.rotation.y.toFixed(3);
  }
  update(strikes);
  frame = requestAnimationFrame(draw);
  try {
    const texture = await new THREE.TextureLoader().loadAsync(artwork);
    if (disposed) { texture.dispose(); return {update() {}, focus() {}, dispose() {}}; }
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
    material.uniforms.portrait.value = texture;
    loaded = true; host.dataset.renderer = 'three'; host.dataset.artwork = artwork;
  } catch {
    host.dataset.renderer = 'fallback'; dispose();
  }
  return {update, focus, dispose};
}
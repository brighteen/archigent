import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { IfcAPI, IFCSPACE } from 'web-ifc';

console.log("🚀 app.js v3 loaded — Pure Three.js Professional BIM Suite");

// ── 원격 로그 ───────────────────────────────────────────────────────────
async function remoteLog(msg) {
    console.log(`[LOG] ${msg}`);
    try {
        await fetch('/api/log', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: msg}) });
    } catch {}
}

// ── IFC 뷰어 클래스 ─────────────────────────────────────────────────────
class IfcViewer {
    constructor(containerId, canvasId, label) {
        this.container = document.getElementById(containerId);
        this.canvas = document.getElementById(canvasId);
        this.label = label;
        this.scene = null;
        this.camera = null;
        this.renderer = null;
        this.controls = null;
        this.currentModel = null;
        this.clippingPlanes = [];
        this.measurements = [];
        this.measureMode = false;
        this.clipMode = false;
        this.measurePoints = [];
        this.measureLine = null;
        this.raycaster = new THREE.Raycaster();
        this.mouse = new THREE.Vector2();
        this.init();
    }

    init() {
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x1c2128);

        const rect = this.container.getBoundingClientRect();
        this.camera = new THREE.PerspectiveCamera(45, rect.width / rect.height, 0.01, 50000);
        this.camera.position.set(15, 15, 15);

        this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true });
        this.renderer.setSize(rect.width, rect.height);
        this.renderer.setPixelRatio(window.devicePixelRatio);
        this.renderer.localClippingEnabled = true;

        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.dampingFactor = 0.05;
        this.controls.zoomSpeed = 0.001;  // 극저속 초정밀 줌
        this.controls.rotateSpeed = 0.3;
        this.controls.screenSpacePanning = true;

        const ambient = new THREE.AmbientLight(0xffffff, 0.6);
        this.scene.add(ambient);
        const dirLight = new THREE.DirectionalLight(0xffffff, 0.9);
        dirLight.position.set(10, 20, 10);
        this.scene.add(dirLight);

        // 그리드 헬퍼
        const grid = new THREE.GridHelper(100, 100, 0x333344, 0x222233);
        this.scene.add(grid);

        // 애니메이션 루프
        const animate = () => {
            requestAnimationFrame(animate);
            this.controls.update();
            this.renderer.render(this.scene, this.camera);
        };
        animate();

        // 리사이즈 핸들러
        new ResizeObserver(() => {
            const r = this.container.getBoundingClientRect();
            this.camera.aspect = r.width / r.height;
            this.camera.updateProjectionMatrix();
            this.renderer.setSize(r.width, r.height);
        }).observe(this.container);

        remoteLog(`[${this.label}] Initialized`);
    }

    async loadIFC(folder, filename) {
        if (!filename) return;
        remoteLog(`[${this.label}] Loading: ${filename}`);
        try {
            // 기존 모델 및 측정값 제거
            if (this.currentModel) {
                this.scene.remove(this.currentModel);
                // 메모리 해제
                this.currentModel.traverse(node => {
                    if (node.isMesh) {
                        node.geometry.dispose();
                        if (Array.isArray(node.material)) node.material.forEach(m => m.dispose());
                        else node.material.dispose();
                    }
                });
            }
            this.measurements.forEach(m => this.scene.remove(m));
            this.measurements = [];
            this.measurePoints = [];

            // 새로운 모델 그룹 생성
            this.currentModel = new THREE.Group();
            this.scene.add(this.currentModel);

            const ifcAPI = new IfcAPI();
            const wasmPath = window.location.origin + "/static/";
            ifcAPI.SetWasmPath(wasmPath); 
            await ifcAPI.Init();

            const res = await fetch(`/api/ifc/${folder}/${filename}`);
            const buf = await res.arrayBuffer();
            const data = new Uint8Array(buf);
            const modelID = ifcAPI.OpenModel(data);

            const matCache = {};

            ifcAPI.StreamAllMeshes(modelID, (flatMesh) => {
                const placedGeoms = flatMesh.geometries;
                for (let i = 0; i < placedGeoms.size(); i++) {
                    const pg = placedGeoms.get(i);
                    const geoData = ifcAPI.GetGeometry(modelID, pg.geometryExpressID);
                    const vArray = new Float32Array(ifcAPI.GetVertexArray(geoData.GetVertexData(), geoData.GetVertexDataSize()));
                    const indices = new Uint32Array(ifcAPI.GetIndexArray(geoData.GetIndexData(), geoData.GetIndexDataSize()));

                    const positions = new Float32Array(vArray.length / 2);
                    const normals = new Float32Array(vArray.length / 2);
                    for (let j = 0; j < vArray.length; j += 6) {
                        const k = j / 2;
                        positions[k] = vArray[j]; positions[k+1] = vArray[j+1]; positions[k+2] = vArray[j+2];
                        normals[k] = vArray[j+3]; normals[k+1] = vArray[j+4]; normals[k+2] = vArray[j+5];
                    }

                    const geo = new THREE.BufferGeometry();
                    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
                    geo.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
                    geo.setIndex(new THREE.BufferAttribute(indices, 1));

                    const color = pg.color || { r: 0.7, g: 0.7, b: 0.7, a: 1.0 };
                    const r = color.x ?? color.r ?? 0.7;
                    const g = color.y ?? color.g ?? 0.7;
                    const b = color.z ?? color.b ?? 0.7;
                    const a = color.w ?? color.a ?? 1.0;

                    const colorKey = `${r.toFixed(2)}_${g.toFixed(2)}_${b.toFixed(2)}_${a.toFixed(2)}`;
                    if (!matCache[colorKey]) {
                        matCache[colorKey] = new THREE.MeshPhongMaterial({
                            color: new THREE.Color(r, g, b),
                            opacity: a,
                            transparent: a < 0.98,
                            side: THREE.DoubleSide,
                            clippingPlanes: this.clippingPlanes,
                        });
                    }

                    const mesh = new THREE.Mesh(geo, matCache[colorKey]);
                    const mat4 = new THREE.Matrix4().fromArray(pg.flatTransformation);
                    mesh.applyMatrix4(mat4);
                    this.currentModel.add(mesh); // 그룹에 추가

                    geoData.delete();
                }
            });

            // 바운딩박스 계산 (그룹 기준)
            const box = new THREE.Box3().setFromObject(this.currentModel);
            const center = box.getCenter(new THREE.Vector3());
            const size = box.getSize(new THREE.Vector3());
            const maxDim = Math.max(size.x, size.y, size.z);
            const fov = this.camera.fov * (Math.PI / 180);
            const dist = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 2.5;

            this.camera.position.set(center.x + dist/1.5, center.y + dist/1.5, center.z + dist/1.5);
            this.camera.updateProjectionMatrix();
            this.controls.target.copy(center);
            this.controls.update();

            this._clipCenter = center.clone();
            this._clipHeight = center.y;

            ifcAPI.CloseModel(modelID);
            remoteLog(`[${this.label}] IFC Loaded: ${filename}`);
        } catch (err) {
            remoteLog(`[${this.label}] Load Error: ${err.message}`);
            console.error(err);
        }
    }

    // ── 단면 (Clipping) 도구 ────────────────────────────────────────
    setClipMode(active) {
        this.clipMode = active;
    }

    addClippingPlane(direction = 'y') {
        if (!this._clipCenter) return;
        let plane;
        if (direction === 'y') {
            plane = new THREE.Plane(new THREE.Vector3(0, -1, 0), this._clipHeight);
        } else if (direction === 'x') {
            plane = new THREE.Plane(new THREE.Vector3(-1, 0, 0), this._clipCenter.x);
        } else {
            plane = new THREE.Plane(new THREE.Vector3(0, 0, -1), this._clipCenter.z);
        }
        this.clippingPlanes.push(plane);
        this.scene.traverse(o => {
            if (o.isMesh) {
                o.material.clippingPlanes = this.clippingPlanes;
            }
        });
        addLog(`단면 추가 (${direction.toUpperCase()}축)`);
    }

    clearClippingPlanes() {
        this.clippingPlanes.length = 0;
        this.scene.traverse(o => {
            if (o.isMesh) o.material.clippingPlanes = [];
        });
        addLog("단면 제거");
    }

    // ── 측정 도구 ─────────────────────────────────────────────────────
    setMeasureMode(active) {
        this.measureMode = active;
        this.measurePoints = [];
        if (!active) {
            this.measurements.forEach(m => this.scene.remove(m));
            this.measurements = [];
        }
    }

    handleClick(event) {
        if (!this.measureMode) return;

        const rect = this.canvas.getBoundingClientRect();
        this.mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
        this.mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

        this.raycaster.setFromCamera(this.mouse, this.camera);
        const meshes = [];
        this.scene.traverse(o => { if (o.isMesh) meshes.push(o); });
        const intersects = this.raycaster.intersectObjects(meshes, false);

        if (intersects.length > 0) {
            const pt = intersects[0].point.clone();
            this.measurePoints.push(pt);

            // 점 표시
            const dotGeom = new THREE.SphereGeometry(0.05);
            const dotMat = new THREE.MeshBasicMaterial({ color: 0x00aaff });
            const dot = new THREE.Mesh(dotGeom, dotMat);
            dot.position.copy(pt);
            this.scene.add(dot);
            this.measurements.push(dot);

            if (this.measurePoints.length === 2) {
                const [p1, p2] = this.measurePoints;
                const dist = p1.distanceTo(p2).toFixed(3);
                
                // 선 그리기
                const lineGeom = new THREE.BufferGeometry().setFromPoints([p1, p2]);
                const lineMat = new THREE.LineBasicMaterial({ color: 0x00aaff });
                const line = new THREE.Line(lineGeom, lineMat);
                this.scene.add(line);
                this.measurements.push(line);

                addLog(`📏 거리: ${dist} m`);
                this.measurePoints = [];
            }
        }
    }

    resetView() {
        const box = new THREE.Box3();
        this.scene.traverse(o => { if (o.isMesh) box.expandByObject(o); });
        if (box.isEmpty()) return;
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const maxDim = Math.max(size.x, size.y, size.z);
        const fov = this.camera.fov * (Math.PI / 180);
        const dist = Math.abs(maxDim / 2 / Math.tan(fov / 2)) * 2.5;
        this.camera.position.set(center.x + dist/1.5, center.y + dist/1.5, center.z + dist/1.5);
        this.controls.target.copy(center);
        this.controls.update();
    }
}

// ── 전역 인스턴스 ─────────────────────────────────────────────────────
let originalViewer = null;
let resultViewer = null;

// ── UI ───────────────────────────────────────────────────────────────
const fileSelect = document.getElementById('file-select');
const runBtn = document.getElementById('run-btn');
const userRequest = document.getElementById('user-request');
const logOutput = document.getElementById('log-output');

function addLog(msg) {
    const div = document.createElement('div');
    div.textContent = `> ${msg}`;
    logOutput.appendChild(div);
    logOutput.scrollTop = logOutput.scrollHeight;
}

async function loadFiles() {
    try {
        const res = await fetch('/api/files');
        const data = await res.json();
        if (data.files && data.files.length > 0) {
            fileSelect.innerHTML = data.files.map(f => `<option value="${f}">${f}</option>`).join('');
            addLog(`파일 ${data.files.length}개 로드됨`);
        } else {
            fileSelect.innerHTML = '<option value="">raw/ 폴더에 IFC 파일 없음</option>';
        }
    } catch (err) {
        addLog(`ERROR: 파일 목록 로드 실패`);
    }
}

function connectSSE() {
    const es = new EventSource('/api/events');
    es.onmessage = ({ data }) => {
        if (data.startsWith("LOG:")) addLog(data.slice(4));
        else if (data.startsWith("STATUS:")) {
            document.querySelectorAll('.step').forEach(s => s.classList.toggle('active', s.dataset.node === data.slice(7)));
        } else if (data.startsWith("RESULT:")) {
            const fname = data.slice(7);
            resultViewer.loadIFC("modified", fname);
            addLog(`수정 완료: ${fname}`);
        } else if (data === "[DONE]") es.close();
        else if (data.startsWith("ERROR:")) addLog(`오류: ${data.slice(6)}`);
    };
    es.onerror = () => es.close();
}

window.addEventListener('DOMContentLoaded', async () => {
    await loadFiles();

    originalViewer = new IfcViewer('original-viewer', 'canvas-original', 'Original');
    resultViewer = new IfcViewer('result-viewer', 'canvas-result', 'Result');
    addLog("System: Professional BIM Suite (Pure Three.js) 가동 완료");

    if (fileSelect.value) {
        originalViewer.loadIFC("raw", fileSelect.value);
    }

    fileSelect.addEventListener('change', () => {
        originalViewer.loadIFC("raw", fileSelect.value);
    });

    document.getElementById('refresh-files-btn').onclick = loadFiles;

    runBtn.addEventListener('click', async () => {
        const filename = fileSelect.value;
        const request = userRequest.value.trim();
        if (!filename || !request) return;
        addLog("에이전트 가동 시작...");
        const res = await fetch('/api/generate', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ifc_filename: filename, user_request: request })
        });
        if (res.ok) connectSSE();
    });

    // ── BIM 도구 버튼 ──────────────────────────────────────────────

    // 📏 측정
    let measureActive = false;
    document.getElementById('measure-btn').onclick = function() {
        measureActive = !measureActive;
        this.classList.toggle('active', measureActive);
        originalViewer.setMeasureMode(measureActive);
        resultViewer.setMeasureMode(measureActive);
        // 단면 비활성화
        if (measureActive) {
            clipActive = false;
            document.getElementById('clip-btn').classList.remove('active');
        }
        addLog(measureActive ? "📏 측정 도구 ON — 두 지점을 클릭하세요" : "📏 측정 도구 OFF");
    };

    // ✂️ 단면
    let clipActive = false;
    document.getElementById('clip-btn').onclick = function() {
        clipActive = !clipActive;
        this.classList.toggle('active', clipActive);
        if (clipActive) {
            measureActive = false;
            document.getElementById('measure-btn').classList.remove('active');
            // 기본 수평(Y) 단면 추가
            originalViewer.addClippingPlane('y');
            resultViewer.addClippingPlane('y');
        } else {
            originalViewer.clearClippingPlanes();
            resultViewer.clearClippingPlanes();
        }
    };

    // 🌳 모델 트리 (간이 버전 — 로그로 표시)
    document.getElementById('tree-btn').onclick = () => {
        addLog("🌳 모델 트리 기능은 향후 업데이트 예정");
    };

    // ℹ️ 속성 정보
    document.getElementById('props-btn').onclick = () => {
        addLog("ℹ️ 객체를 클릭하면 속성이 표시됩니다");
    };

    // 📷 뷰 초기화
    document.getElementById('reset-cam-btn').onclick = () => {
        originalViewer.resetView();
        resultViewer.resetView();
        addLog("📷 뷰 초기화");
    };

    // 캔버스 클릭 이벤트 (측정 도구)
    document.getElementById('canvas-original').addEventListener('click', (e) => {
        originalViewer.handleClick(e);
    });
    document.getElementById('canvas-result').addEventListener('click', (e) => {
        resultViewer.handleClick(e);
    });
});

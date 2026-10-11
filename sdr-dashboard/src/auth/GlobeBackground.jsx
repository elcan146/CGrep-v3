import React, { useRef, useMemo, useEffect } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, Sphere, MeshDistortMaterial } from '@react-three/drei';

const FPS = 10; // render auth globe at 10 fps instead of display refresh rate

const Globe = () => {
  const meshRef = useRef();

  useFrame(() => {
    if (meshRef.current) {
      meshRef.current.rotation.y += 0.0015;
    }
  });

  return (
    <Sphere ref={meshRef} args={[1, 14, 14]} scale={2.5}>
      <MeshDistortMaterial
        color="#00e5ff"
        attach="material"
        distort={0.15}
        speed={0.4}
        roughness={0.2}
        metalness={0.8}
        wireframe
      />
    </Sphere>
  );
};

const Particles = () => {
  const count = 40;
  const positions = useMemo(() => {
    const pos = new Float32Array(count * 3);
    for (let i = 0; i < count * 3; i++) {
      pos[i] = (Math.random() - 0.5) * 10;
    }
    return pos;
  }, []);

  return (
    <points>
      <bufferGeometry>
        <bufferAttribute
          attach="attributes-position"
          count={count}
          array={positions}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial
        size={0.025}
        color="#00e5ff"
        transparent
        opacity={0.5}
      />
    </points>
  );
};

const AutoInvalidator = () => {
  const { invalidate } = useThree();

  useEffect(() => {
    let id = null;

    const start = () => {
      if (id) return;
      const loop = () => {
        invalidate();
        id = setTimeout(loop, 1000 / FPS);
      };
      loop();
    };

    const handleVisibility = () => {
      if (document.hidden) {
        if (id) { clearTimeout(id); id = null; }
      } else {
        start();
      }
    };

    start();
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      if (id) clearTimeout(id);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [invalidate]);

  return null;
};

const GlobeBackground = () => {
  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      width: '100%',
      height: '100%',
      zIndex: 0,
    }}>
      <Canvas
        dpr={[1, 1]}
        gl={{ antialias: false, powerPreference: 'low-power' }}
        camera={{ position: [0, 0, 5], fov: 45 }}
        style={{ background: '#05070b' }}
        frameloop="demand"
      >
        <ambientLight intensity={0.5} />
        <pointLight position={[10, 10, 10]} intensity={1} color="#00e5ff" />
        <pointLight position={[-10, -10, -10]} intensity={0.5} color="#4a00e0" />
        <Globe />
        <Particles />
        <AutoInvalidator />
        <OrbitControls
          enableZoom={false}
          enablePan={false}
          autoRotate
          autoRotateSpeed={0.3}
        />
      </Canvas>
    </div>
  );
};

export default GlobeBackground;

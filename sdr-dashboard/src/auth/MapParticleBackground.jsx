import React, { useCallback, useMemo } from 'react';
import Particles from '@tsparticles/react';
import { loadSlim } from '@tsparticles/slim';

const MapParticleBackground = () => {
  const particlesInit = useCallback(async (engine) => {
    await loadSlim(engine);
  }, []);

  const options = useMemo(() => ({
    background: {
      color: {
        value: '#05070b',
      },
    },
    fpsLimit: 30,
    interactivity: {
      events: {
        onClick: { enable: false },
        onHover: { enable: false },
        resize: true,
      },
    },
    particles: {
      color: {
        value: ['#00e5ff', '#4a00e0', '#ffaf00'],
      },
      links: {
        color: '#00e5ff',
        distance: 90,
        enable: true,
        opacity: 0.25,
        width: 0.8,
      },
      move: {
        direction: 'none',
        enable: true,
        outModes: {
          default: 'bounce',
        },
        random: true,
        speed: 0.6,
        straight: false,
      },
      number: {
        density: {
          enable: true,
          area: 1000,
        },
        value: 25,
      },
      opacity: {
        value: 0.6,
      },
      shape: {
        type: 'circle',
      },
      size: {
        value: { min: 1, max: 2.5 },
      },
    },
    detectRetina: false,
  }), []);

  return (
    <Particles
      id="map-particles"
      init={particlesInit}
      options={options}
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        zIndex: 0,
      }}
    />
  );
};

export default MapParticleBackground;

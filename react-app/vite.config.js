import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import net from 'net';
import { spawn } from 'child_process';

function autoStartFastApiBackend() {
  let backendProc = null;
  return {
    name: 'vasco-auto-fastapi-backend',
    configureServer() {
      const rootDir = path.resolve(__dirname, '..');
      const pythonExe = path.resolve(rootDir, '.venv', 'Scripts', 'python.exe');

      const socket = new net.Socket();
      socket.setTimeout(600);
      socket.once('connect', () => {
        socket.destroy();
      });
      socket.once('error', () => {
        socket.destroy();
        console.log('\n🚀 [Vasco] Démarrage automatique du backend Python FastAPI sur http://127.0.0.1:8000 ...');
        backendProc = spawn(
          pythonExe,
          ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000'],
          { cwd: rootDir, stdio: 'inherit', windowsHide: true }
        );
      });
      socket.connect(8000, '127.0.0.1');
    },
    closeBundle() {
      if (backendProc) {
        try { backendProc.kill(); } catch (_) {}
      }
    },
  };
}

export default defineConfig(({ command }) => ({
  plugins: [react(), autoStartFastApiBackend()],
  base: command === 'build' ? '/static/' : '/',
  build: {
    outDir: path.resolve(__dirname, '../frontend'),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
}));

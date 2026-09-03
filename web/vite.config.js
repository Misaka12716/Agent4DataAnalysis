import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';
var backend = 'http://127.0.0.1:52716';
export default defineConfig({
    plugins: [vue()],
    resolve: {
        alias: {
            '@': fileURLToPath(new URL('./src', import.meta.url)),
        },
    },
    server: {
        port: 5173,
        proxy: {
            '/project': backend,
            '/session': backend,
            '/run-analysis': backend,
            '/upload': backend,
            '/health': backend,
        },
    },
});

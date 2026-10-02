// AUD-27: evaluate BACKEND_URL when Vercel loads the deployment configuration.
// Static vercel.json strings do not interpolate environment variables.
const raw = process.env.BACKEND_URL?.trim()
if (!raw) throw new Error('BACKEND_URL must be configured before loading the Vercel configuration')
let backend
try {
  backend = new URL(raw)
} catch {
  throw new Error('BACKEND_URL must be an absolute HTTP(S) origin')
}
if (!['http:', 'https:'].includes(backend.protocol) || backend.username || backend.password ||
    backend.search || backend.hash || backend.pathname !== '/') {
  throw new Error('BACKEND_URL must be an HTTP(S) origin without credentials, path, query or fragment')
}

export const config = {
  installCommand: 'npm ci --prefix frontend',
  buildCommand: 'npm --prefix frontend run build',
  outputDirectory: 'frontend/dist',
  framework: 'vite',
  rewrites: [{ source: '/api/:path*', destination: `${backend.origin}/api/:path*` }],
  headers: [{
    source: '/assets/(.*)',
    headers: [{ key: 'Cache-Control', value: 'public, max-age=31536000, immutable' }],
  }],
}

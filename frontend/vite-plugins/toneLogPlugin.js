import fs from 'node:fs'
import path from 'node:path'

/**
 * Dev-only endpoint for the tone feature's debug logger
 * (src/features/tone/debug/toneLogger.ts): appends each POSTed body to a text file.
 */
export function toneLogPlugin({ endpoint = '/__tone-log', file = 'logs/tone-debug.log' } = {}) {
  return {
    name: 'tone-log',
    apply: 'serve',
    configureServer(server) {
      const out = path.resolve(server.config.root, file)
      fs.mkdirSync(path.dirname(out), { recursive: true })
      server.config.logger.info(`  tone debug log → ${out}`)

      server.middlewares.use(endpoint, (req, res) => {
        if (req.method !== 'POST') {
          res.statusCode = 405
          return res.end()
        }
        let body = ''
        req.setEncoding('utf8')
        req.on('data', (chunk) => (body += chunk))
        req.on('end', () => {
          fs.appendFile(out, body, (err) => {
            res.statusCode = err ? 500 : 204
            res.end()
          })
        })
      })
    },
  }
}

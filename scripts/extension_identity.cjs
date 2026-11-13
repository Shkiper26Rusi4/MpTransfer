// Keep a stable unpacked extension identity across copies and upgrades.
const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');
const manifestPath = path.join(__dirname, '..', 'extension', 'manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
if (!manifest.key) {
  const {publicKey} = crypto.generateKeyPairSync('rsa', {modulusLength: 2048});
  manifest.key = publicKey.export({type:'spki', format:'der'}).toString('base64');
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
}
const id = [...crypto.createHash('sha256').update(Buffer.from(manifest.key, 'base64')).digest('hex').slice(0,32)].map(c => String.fromCharCode(97+parseInt(c,16))).join('');
console.log(id);

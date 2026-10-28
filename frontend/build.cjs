"use strict";
// Zero-dependency production build: emits the SPA placeholder artifact.
// No bundler, no registry fetches — `npm run build` exits 0 offline in seconds.
const fs = require("fs");
const path = require("path");

const distDir = path.join(__dirname, "dist");
const outFile = path.join(distDir, "index.html");

const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ELSEWHERE</title>
  <style>
    body { margin: 0; font-family: Georgia, serif; background: #F7F3EC; color: #1a1a1a;
           display: flex; align-items: center; justify-content: center; min-height: 100vh; }
    main { text-align: center; }
    h1 { letter-spacing: 0.35em; font-size: 3rem; margin: 0; }
    p { opacity: 0.7; }
  </style>
</head>
<body>
  <main>
    <h1>ELSEWHERE</h1>
    <p>Feel at home, elsewhere.</p>
  </main>
</body>
</html>
`;

fs.mkdirSync(distDir, { recursive: true });
fs.writeFileSync(outFile, html);
console.log(`build: wrote ${path.relative(process.cwd(), outFile)}`);

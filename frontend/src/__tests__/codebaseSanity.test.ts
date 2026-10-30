import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';

function getAllFiles(dirPath: string, arrayOfFiles: string[] = []): string[] {
  const files: string[] = fs.readdirSync(dirPath);

  files.forEach((file: string) => {
    const fullPath = path.join(dirPath, file);
    if (fs.statSync(fullPath).isDirectory()) {
      getAllFiles(fullPath, arrayOfFiles);
    } else {
      arrayOfFiles.push(fullPath);
    }
  });

  return arrayOfFiles;
}

describe('Frontend codebase sanity checks', () => {
  const srcDir = path.resolve(process.cwd(), 'src');
  const allFiles = getAllFiles(srcDir);

  // Exclude this test file itself from the scan to avoid false positives
  const nonTestFiles = allFiles.filter((f: string) => !f.includes('__tests__'));

  it('ensures nothing under frontend/src contains X-Admin-Token, ADMIN_TOKEN, vault or payer', () => {
    const forbiddenPatterns = [
      'X-Admin-' + 'Token',
      'ADMIN_' + 'TOKEN',
      'v' + 'ault',
      'p' + 'ayer',
    ];

    const violations: { file: string; pattern: string; line: number }[] = [];

    nonTestFiles.forEach((file: string) => {
      const content = fs.readFileSync(file, 'utf8');
      const lines: string[] = content.split('\n');

      lines.forEach((line: string, lineIndex: number) => {
        forbiddenPatterns.forEach((pattern: string) => {
          if (line.toLowerCase().includes(pattern.toLowerCase())) {
            violations.push({
              file: path.relative(srcDir, file),
              pattern,
              line: lineIndex + 1,
            });
          }
        });
      });
    });

    expect(violations, `Found forbidden terms in codebase: ${JSON.stringify(violations)}`).toEqual([]);
  });

  it('ensures only src/api/client.ts calls fetch(', () => {
    const fetchRegex = /\bfetch\(/;
    const filesWithFetch: string[] = [];

    nonTestFiles.forEach((file: string) => {
      const content = fs.readFileSync(file, 'utf8');
      if (fetchRegex.test(content)) {
        filesWithFetch.push(path.relative(srcDir, file).replace(/\\/g, '/'));
      }
    });

    expect(filesWithFetch).toEqual(['api/client.ts']);
  });
});

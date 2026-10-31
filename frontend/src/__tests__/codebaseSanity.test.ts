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

  // Exclude test files from the scan to avoid false positives
  const nonTestFiles = allFiles.filter((f: string) => !f.includes('__tests__'));

  it('ensures token header name appears ONLY in src/features/rules/adminClient.ts and other forbidden terms are absent', () => {
    const tokenHeaderPattern = 'X-Admin-' + 'Token';
    const forbiddenPatterns = [
      'ADMIN_' + 'TOKEN',
      'v' + 'ault',
      'p' + 'ayer',
    ];

    const violations: { file: string; pattern: string; line: number }[] = [];
    const filesWithTokenHeader: string[] = [];

    nonTestFiles.forEach((file: string) => {
      const relPath = path.relative(srcDir, file).replace(/\\/g, '/');
      const content = fs.readFileSync(file, 'utf8');
      const lines: string[] = content.split('\n');

      lines.forEach((line: string, lineIndex: number) => {
        // Forbidden across the entire codebase
        forbiddenPatterns.forEach((pattern: string) => {
          if (line.toLowerCase().includes(pattern.toLowerCase())) {
            violations.push({
              file: relPath,
              pattern,
              line: lineIndex + 1,
            });
          }
        });

        // Token header name is allowed ONLY in src/features/rules/adminClient.ts
        if (line.toLowerCase().includes(tokenHeaderPattern.toLowerCase())) {
          if (relPath !== 'features/rules/adminClient.ts') {
            violations.push({
              file: relPath,
              pattern: tokenHeaderPattern,
              line: lineIndex + 1,
            });
          } else {
            if (!filesWithTokenHeader.includes(relPath)) {
              filesWithTokenHeader.push(relPath);
            }
          }
        }
      });
    });

    expect(violations, `Found forbidden terms in codebase: ${JSON.stringify(violations)}`).toEqual([]);
    expect(filesWithTokenHeader).toEqual(['features/rules/adminClient.ts']);
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

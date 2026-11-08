// A QR code encoder for the judge page (OP-411). Byte mode, versions 1 to 40, the
// four error-correction levels, mask chosen by the standard's penalty rules. Written
// for this project from ISO/IEC 18004; the tables below are the standard's. No
// dependency, no DOM: it runs at build time on the server and returns an SVG path.
//
//   const code = encodeQr("https://openpath-lyart.vercel.app");
//   <svg viewBox={qrViewBox(code)}><path d={qrPath(code)} /></svg>
//
// Verified against an independent encoder (the Python `qrcode` package) for every
// mask, and by decoding the rendered page with OpenCV; see docs/design/DESIGN.md.

export type EcLevel = "L" | "M" | "Q" | "H";

export type QrCode = {
  /** 1 to 40. */
  version: number;
  /** Modules per side: 17 + 4 × version. */
  size: number;
  level: EcLevel;
  /** The mask that was applied, 0 to 7. */
  mask: number;
  /** modules[y][x] is true where the module is dark. */
  modules: boolean[][];
};

export type EncodeOptions = {
  level?: EcLevel;
  /** Force a mask (0 to 7) instead of choosing the lowest penalty. For tests. */
  mask?: number;
  minVersion?: number;
  maxVersion?: number;
};

/** The standard's quiet zone: four light modules on every side. */
export const QUIET_ZONE = 4;

const LEVEL_INDEX: Record<EcLevel, number> = { L: 0, M: 1, Q: 2, H: 3 };
// The two format bits for each level (L=01, M=00, Q=11, H=10).
const LEVEL_FORMAT_BITS: Record<EcLevel, number> = { L: 1, M: 0, Q: 3, H: 2 };

// Error-correction codewords per block, by level (L, M, Q, H) and version (1 to 40).
const ECC_CODEWORDS_PER_BLOCK: ReadonlyArray<ReadonlyArray<number>> = [
  [7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  [10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
  [13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  [17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30]
];

// Number of error-correction blocks, by level (L, M, Q, H) and version (1 to 40).
const NUM_ERROR_CORRECTION_BLOCKS: ReadonlyArray<ReadonlyArray<number>> = [
  [1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
  [1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
  [1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
  [1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81]
];

// Penalty weights from the standard (N1 to N4).
const PENALTY_N1 = 3;
const PENALTY_N2 = 3;
const PENALTY_N3 = 40;
const PENALTY_N4 = 10;

/** Encode text (as UTF-8, byte mode) at the smallest version that holds it. */
export function encodeQr(text: string, options: EncodeOptions = {}): QrCode {
  const level = options.level ?? "M";
  const minVersion = options.minVersion ?? 1;
  const maxVersion = options.maxVersion ?? 40;
  if (!(1 <= minVersion && minVersion <= maxVersion && maxVersion <= 40)) {
    throw new RangeError("QR version range must lie within 1 to 40");
  }
  const bytes = new TextEncoder().encode(text);

  let version = minVersion;
  for (;;) {
    const capacityBits = dataCodewords(version, level) * 8;
    if (usedBits(bytes.length, version) <= capacityBits) break;
    if (version >= maxVersion) throw new RangeError("Text is too long for a QR code at this level");
    version += 1;
  }

  // Segment: mode indicator, character count, the bytes.
  const bits: number[] = [];
  appendBits(bits, 0x4, 4);
  appendBits(bits, bytes.length, countBits(version));
  for (const byte of bytes) appendBits(bits, byte, 8);

  // Terminator, pad to a byte, then the alternating pad codewords.
  const capacityBits = dataCodewords(version, level) * 8;
  appendBits(bits, 0, Math.min(4, capacityBits - bits.length));
  appendBits(bits, 0, (8 - (bits.length % 8)) % 8);
  for (let pad = 0xec; bits.length < capacityBits; pad ^= 0xec ^ 0x11) appendBits(bits, pad, 8);

  const data = new Uint8Array(bits.length / 8);
  bits.forEach((bit, index) => {
    data[index >>> 3] |= bit << (7 - (index & 7));
  });

  return buildCode(version, level, data, options.mask ?? -1);
}

/** The SVG `viewBox` for a code with its quiet zone. */
export function qrViewBox(code: QrCode, quiet: number = QUIET_ZONE): string {
  const extent = code.size + 2 * quiet;
  return `0 0 ${extent} ${extent}`;
}

/**
 * The dark modules as one SVG path, one unit per module, offset by the quiet zone.
 * Horizontal runs are merged so the path stays short; `shape-rendering="crispEdges"`
 * on the <svg> keeps module edges sharp at any size.
 */
export function qrPath(code: QrCode, quiet: number = QUIET_ZONE): string {
  const parts: string[] = [];
  code.modules.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      if (!row[x]) {
        x += 1;
        continue;
      }
      let run = 1;
      while (x + run < row.length && row[x + run]) run += 1;
      parts.push(`M${x + quiet} ${y + quiet}h${run}v1h-${run}z`);
      x += run;
    }
  });
  return parts.join("");
}

// ---------------------------------------------------------------------------
// Capacity

function countBits(version: number): number {
  return version <= 9 ? 8 : 16;
}

function usedBits(byteCount: number, version: number): number {
  return 4 + countBits(version) + 8 * byteCount;
}

/** Modules available for data and error correction, after the function patterns. */
function rawDataModules(version: number): number {
  let result = (16 * version + 128) * version + 64;
  if (version >= 2) {
    const numAlign = Math.floor(version / 7) + 2;
    result -= (25 * numAlign - 10) * numAlign - 55;
    if (version >= 7) result -= 36;
  }
  return result;
}

function dataCodewords(version: number, level: EcLevel): number {
  const index = LEVEL_INDEX[level];
  return (
    Math.floor(rawDataModules(version) / 8) -
    ECC_CODEWORDS_PER_BLOCK[index][version - 1] * NUM_ERROR_CORRECTION_BLOCKS[index][version - 1]
  );
}

function appendBits(bits: number[], value: number, length: number): void {
  for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
}

// ---------------------------------------------------------------------------
// Reed-Solomon over GF(2^8) with the QR polynomial x^8 + x^4 + x^3 + x^2 + 1

function gfMultiply(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z;
}

function rsDivisor(degree: number): Uint8Array {
  const result = new Uint8Array(degree);
  result[degree - 1] = 1; // the monic polynomial x^0
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      result[j] = gfMultiply(result[j], root);
      if (j + 1 < degree) result[j] ^= result[j + 1];
    }
    root = gfMultiply(root, 0x02);
  }
  return result;
}

function rsRemainder(data: Uint8Array, divisor: Uint8Array): Uint8Array {
  const result = new Uint8Array(divisor.length);
  for (const byte of data) {
    const factor = byte ^ result[0];
    result.copyWithin(0, 1);
    result[result.length - 1] = 0;
    divisor.forEach((coefficient, i) => {
      result[i] ^= gfMultiply(coefficient, factor);
    });
  }
  return result;
}

/** Split the data into blocks, add error correction to each, and interleave them. */
function addErrorCorrection(data: Uint8Array, version: number, level: EcLevel): Uint8Array {
  const index = LEVEL_INDEX[level];
  const numBlocks = NUM_ERROR_CORRECTION_BLOCKS[index][version - 1];
  const blockEccLen = ECC_CODEWORDS_PER_BLOCK[index][version - 1];
  const rawCodewords = Math.floor(rawDataModules(version) / 8);
  const numShortBlocks = numBlocks - (rawCodewords % numBlocks);
  const shortBlockLen = Math.floor(rawCodewords / numBlocks);

  const blocks: Uint8Array[] = [];
  const divisor = rsDivisor(blockEccLen);
  for (let i = 0, offset = 0; i < numBlocks; i++) {
    const datLen = shortBlockLen - blockEccLen + (i < numShortBlocks ? 0 : 1);
    const dat = data.subarray(offset, offset + datLen);
    offset += datLen;
    const block = new Uint8Array(shortBlockLen + 1);
    block.set(dat, 0);
    block.set(rsRemainder(dat, divisor), shortBlockLen + 1 - blockEccLen);
    blocks.push(block);
  }

  const result = new Uint8Array(rawCodewords);
  let k = 0;
  for (let i = 0; i < shortBlockLen + 1; i++) {
    blocks.forEach((block, j) => {
      // Skip the padding byte in short blocks.
      if (i !== shortBlockLen - blockEccLen || j >= numShortBlocks) result[k++] = block[i];
    });
  }
  return result;
}

// ---------------------------------------------------------------------------
// The symbol

type Grid = { size: number; modules: boolean[][]; isFunction: boolean[][] };

function buildCode(version: number, level: EcLevel, data: Uint8Array, forcedMask: number): QrCode {
  const size = version * 4 + 17;
  const grid: Grid = {
    size,
    modules: Array.from({ length: size }, () => new Array<boolean>(size).fill(false)),
    isFunction: Array.from({ length: size }, () => new Array<boolean>(size).fill(false))
  };

  drawFunctionPatterns(grid, version, level);
  drawCodewords(grid, addErrorCorrection(data, version, level));

  let mask = forcedMask;
  if (mask === -1) {
    let best = Infinity;
    for (let i = 0; i < 8; i++) {
      applyMask(grid, i);
      drawFormatBits(grid, level, i);
      const penalty = penaltyScore(grid);
      if (penalty < best) {
        best = penalty;
        mask = i;
      }
      applyMask(grid, i); // XOR again to undo
    }
  }
  if (!(0 <= mask && mask <= 7)) throw new RangeError("Mask must be 0 to 7");
  applyMask(grid, mask);
  drawFormatBits(grid, level, mask);

  return { version, size, level, mask, modules: grid.modules };
}

function setFunctionModule(grid: Grid, x: number, y: number, isDark: boolean): void {
  grid.modules[y][x] = isDark;
  grid.isFunction[y][x] = true;
}

function drawFunctionPatterns(grid: Grid, version: number, level: EcLevel): void {
  const { size } = grid;
  // Timing patterns.
  for (let i = 0; i < size; i++) {
    setFunctionModule(grid, 6, i, i % 2 === 0);
    setFunctionModule(grid, i, 6, i % 2 === 0);
  }
  // Finder patterns, with their separators.
  drawFinderPattern(grid, 3, 3);
  drawFinderPattern(grid, size - 4, 3);
  drawFinderPattern(grid, 3, size - 4);
  // Alignment patterns, except where a finder sits.
  const positions = alignmentPositions(version);
  const last = positions.length - 1;
  positions.forEach((x, i) => {
    positions.forEach((y, j) => {
      const onFinder = (i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0);
      if (!onFinder) drawAlignmentPattern(grid, x, y);
    });
  });
  // Reserve the format and version areas; the real bits come with the mask.
  drawFormatBits(grid, level, 0);
  drawVersion(grid, version);
}

function drawFinderPattern(grid: Grid, cx: number, cy: number): void {
  for (let dy = -4; dy <= 4; dy++) {
    for (let dx = -4; dx <= 4; dx++) {
      const distance = Math.max(Math.abs(dx), Math.abs(dy));
      const x = cx + dx;
      const y = cy + dy;
      if (0 <= x && x < grid.size && 0 <= y && y < grid.size) {
        setFunctionModule(grid, x, y, distance !== 2 && distance !== 4);
      }
    }
  }
}

function drawAlignmentPattern(grid: Grid, cx: number, cy: number): void {
  for (let dy = -2; dy <= 2; dy++) {
    for (let dx = -2; dx <= 2; dx++) {
      setFunctionModule(grid, cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }
}

function alignmentPositions(version: number): number[] {
  if (version === 1) return [];
  const numAlign = Math.floor(version / 7) + 2;
  const size = version * 4 + 17;
  const step = version === 32 ? 26 : Math.ceil((version * 4 + 4) / (numAlign * 2 - 2)) * 2;
  const result = [6];
  for (let pos = size - 7; result.length < numAlign; pos -= step) result.splice(1, 0, pos);
  return result;
}

/** The 15 format bits (level and mask, BCH-coded, masked) in both of their places. */
function drawFormatBits(grid: Grid, level: EcLevel, mask: number): void {
  const data = (LEVEL_FORMAT_BITS[level] << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  const bits = ((data << 10) | rem) ^ 0x5412;
  const { size } = grid;
  const bit = (i: number) => ((bits >>> i) & 1) !== 0;
  // Around the top-left finder.
  for (let i = 0; i <= 5; i++) setFunctionModule(grid, 8, i, bit(i));
  setFunctionModule(grid, 8, 7, bit(6));
  setFunctionModule(grid, 8, 8, bit(7));
  setFunctionModule(grid, 7, 8, bit(8));
  for (let i = 9; i < 15; i++) setFunctionModule(grid, 14 - i, 8, bit(i));
  // Beside the other two finders.
  for (let i = 0; i < 8; i++) setFunctionModule(grid, size - 1 - i, 8, bit(i));
  for (let i = 8; i < 15; i++) setFunctionModule(grid, 8, size - 15 + i, bit(i));
  setFunctionModule(grid, 8, size - 8, true); // the dark module
}

/** The 18 version bits, for versions 7 and up. */
function drawVersion(grid: Grid, version: number): void {
  if (version < 7) return;
  let rem = version;
  for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
  const bits = (version << 12) | rem;
  const { size } = grid;
  for (let i = 0; i < 18; i++) {
    const bit = ((bits >>> i) & 1) !== 0;
    const a = size - 11 + (i % 3);
    const b = Math.floor(i / 3);
    setFunctionModule(grid, a, b, bit);
    setFunctionModule(grid, b, a, bit);
  }
}

/** Place the codewords in the zigzag, two columns at a time, skipping column 6. */
function drawCodewords(grid: Grid, data: Uint8Array): void {
  const { size } = grid;
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? size - 1 - vert : vert;
        if (!grid.isFunction[y][x] && i < data.length * 8) {
          grid.modules[y][x] = ((data[i >>> 3] >>> (7 - (i & 7))) & 1) !== 0;
          i += 1;
        }
      }
    }
  }
}

function maskBit(mask: number, x: number, y: number): boolean {
  switch (mask) {
    case 0:
      return (x + y) % 2 === 0;
    case 1:
      return y % 2 === 0;
    case 2:
      return x % 3 === 0;
    case 3:
      return (x + y) % 3 === 0;
    case 4:
      return (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0;
    case 5:
      return ((x * y) % 2) + ((x * y) % 3) === 0;
    case 6:
      return (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
    case 7:
      return (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
    default:
      throw new RangeError("Mask must be 0 to 7");
  }
}

/** XOR the mask onto the data modules. Applying it twice undoes it. */
function applyMask(grid: Grid, mask: number): void {
  for (let y = 0; y < grid.size; y++) {
    for (let x = 0; x < grid.size; x++) {
      if (!grid.isFunction[y][x] && maskBit(mask, x, y)) grid.modules[y][x] = !grid.modules[y][x];
    }
  }
}

// ---------------------------------------------------------------------------
// Penalty score (the standard's four rules)

function penaltyScore(grid: Grid): number {
  const { size, modules } = grid;
  let result = 0;

  // Rule 1 and 3 across rows.
  for (let y = 0; y < size; y++) {
    let runColor = false;
    let runX = 0;
    const history = [0, 0, 0, 0, 0, 0, 0];
    for (let x = 0; x < size; x++) {
      if (modules[y][x] === runColor) {
        runX += 1;
        if (runX === 5) result += PENALTY_N1;
        else if (runX > 5) result += 1;
      } else {
        finderPenaltyAddHistory(size, runX, history);
        if (!runColor) result += finderPenaltyCountPatterns(history) * PENALTY_N3;
        runColor = modules[y][x];
        runX = 1;
      }
    }
    result += finderPenaltyTerminateAndCount(size, runColor, runX, history) * PENALTY_N3;
  }
  // Rule 1 and 3 down columns.
  for (let x = 0; x < size; x++) {
    let runColor = false;
    let runY = 0;
    const history = [0, 0, 0, 0, 0, 0, 0];
    for (let y = 0; y < size; y++) {
      if (modules[y][x] === runColor) {
        runY += 1;
        if (runY === 5) result += PENALTY_N1;
        else if (runY > 5) result += 1;
      } else {
        finderPenaltyAddHistory(size, runY, history);
        if (!runColor) result += finderPenaltyCountPatterns(history) * PENALTY_N3;
        runColor = modules[y][x];
        runY = 1;
      }
    }
    result += finderPenaltyTerminateAndCount(size, runColor, runY, history) * PENALTY_N3;
  }

  // Rule 2: 2 × 2 blocks of one colour.
  for (let y = 0; y < size - 1; y++) {
    for (let x = 0; x < size - 1; x++) {
      const color = modules[y][x];
      if (color === modules[y][x + 1] && color === modules[y + 1][x] && color === modules[y + 1][x + 1]) {
        result += PENALTY_N2;
      }
    }
  }

  // Rule 4: how far the dark proportion is from 50%, in steps of 5%.
  let dark = 0;
  for (const row of modules) for (const isDark of row) if (isDark) dark += 1;
  const total = size * size;
  const k = Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1;
  result += k * PENALTY_N4;
  return result;
}

/** How many finder-like 1:1:3:1:1 runs, with a 4-wide light margin, end here. */
function finderPenaltyCountPatterns(history: number[]): number {
  const n = history[1];
  const core = n > 0 && history[2] === n && history[3] === n * 3 && history[4] === n && history[5] === n;
  return (core && history[0] >= n * 4 && history[6] >= n ? 1 : 0) + (core && history[6] >= n * 4 && history[0] >= n ? 1 : 0);
}

function finderPenaltyTerminateAndCount(size: number, runColor: boolean, runLength: number, history: number[]): number {
  let length = runLength;
  if (runColor) {
    finderPenaltyAddHistory(size, length, history);
    length = 0;
  }
  length += size; // the quiet zone counts as light
  finderPenaltyAddHistory(size, length, history);
  return finderPenaltyCountPatterns(history);
}

function finderPenaltyAddHistory(size: number, runLength: number, history: number[]): void {
  let length = runLength;
  if (history[0] === 0) length += size; // the first run borders the quiet zone
  history.pop();
  history.unshift(length);
}

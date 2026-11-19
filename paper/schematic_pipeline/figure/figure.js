// Benchmark pipeline for "Unknown Is Easy, Absorbed Is Hard" (PVLDB EA&B).
// Dark input card on the left, model pill with an output card in the middle, light card with a check table on the right.
export const figure = { width: 178, height: 52, name: 'pipeline', format: 'journal' };

const INK = '#0b0b0b', INK2 = '#52514e', RULE = '#cfcdc6', DARK = '#1f2328';
const UNK = '#2a78d6', ABS = '#eb6834', CLS = '#1baf7a', GOOD = '#0ca30c', BAD = '#d03b3b';
const MONO = 'Consolas';

export default function draw(F, L) {
  const M = L.mm, R = F.root;
  const T = (x, y, s, o = {}) => L.text(R, M(x), M(y), s, { size: 6, color: INK, ...o });

  // ---------------- A: the generated item (dark card)
  const ax = 1, ay = 2, aw = 61, ah = 48;
  L.rect(R, M(ax), M(ay), M(aw), M(ah), { fill: DARK, r: M(2.2), part: 'cardA' });
  T(ax + 3.5, ay + 5.2, 'Item (generated)', { size: 7, weight: 'bold', color: '#ffffff' });
  const sql = [
    ['CREATE TABLE t (id INT PRIMARY KEY,', 0],
    ['a INT, b INT);', 16],
    ['INSERT INTO t VALUES (1,**NULL**,3),', 0],
    ['(2,**NULL**,9), (3,7,9), (4,1,9);', 2],
    ['SELECT id FROM t', 0],
    ['WHERE a > 5 OR b < 5;', 2],
  ];
  sql.forEach(([s, ind], i) => {
    const gap = i >= 2 ? 1.2 : 0, gap2 = i >= 4 ? 1.2 : 0;
    T(ax + 3.5 + ind * 1.32, ay + 11.2 + i * 3.25 + gap + gap2, s, { size: 6, family: MONO, color: '#f0efec' });
  });
  // gold badge
  const by = ay + 35.5;
  L.line(R, [M(ax + 3.5), M(by - 2.6)], [M(ax + aw - 3.5), M(by - 2.6)], { stroke: '#4a4f57', width: 0.5 });
  T(ax + 3.5, by + 1.2, 'gold answer: [1, 3]', { size: 6, weight: 'bold', color: '#ffffff' });
  const chips = ['PostgreSQL', 'SQLite', 'DuckDB', 'reference evaluator'];
  const cw = [13.2, 8.4, 9.4, 17.8];
  let cx = ax + 3.5;
  const cy = by + 4.3;
  chips.forEach((c, i) => {
    L.rect(R, M(cx), M(cy), M(cw[i]), M(4.2), { fill: '#33383f', stroke: '#5b616a', width: 0.4, r: M(1.0) });
    T(cx + cw[i] / 2, cy + 2.15, c, { size: 5.2, anchor: 'middle', valign: 'middle', color: '#f0efec' });
    cx += cw[i];
    if (i < chips.length - 1) { T(cx + 0.65, cy + 2.15, '=', { size: 5.6, anchor: 'middle', valign: 'middle', color: '#c3c2b7' }); cx += 1.3; }
  });
  F.anchor('A.out', R, M(ax + aw), M(ay + 8));

  // ---------------- B: model and its predicted result
  const bx = 70, bw = 32;
  L.rect(R, M(bx), M(ay + 4), M(bw), M(8), { fill: '#f0efec', stroke: INK2, width: 0.6, r: M(4) });
  T(bx + bw / 2, ay + 8, 'Language model', { size: 7, weight: 'bold', anchor: 'middle', valign: 'middle' });
  F.anchor('B.in', R, M(bx), M(ay + 8));
  F.anchor('B.pill', R, M(bx + bw / 2), M(ay + 12));
  const ry = ay + 18.5, rh = 29.5;
  L.rect(R, M(bx), M(ry), M(bw), M(rh), { fill: '#ffffff', stroke: RULE, width: 0.6, r: M(2) });
  F.anchor('B.res', R, M(bx + bw / 2), M(ry));
  T(bx + bw / 2, ry + 4.6, 'Predicted result', { size: 6.5, weight: 'bold', anchor: 'middle' });
  L.rect(R, M(bx + 4), M(ry + 7.6), M(bw - 8), M(6.2), { fill: '#f6f6f4', stroke: RULE, width: 0.4, r: M(1) });
  T(bx + bw / 2, ry + 10.7, 'Answer: [3]', { size: 7, family: MONO, anchor: 'middle', valign: 'middle' });
  T(bx + bw / 2, ry + 18.6, 'illustrative: the shortcut', { size: 5.4, anchor: 'middle', color: INK2, italic: true });
  T(bx + bw / 2, ry + 21.6, '“a NULL makes the', { size: 5.4, anchor: 'middle', color: INK2, italic: true });
  T(bx + bw / 2, ry + 24.6, 'condition fail” (wk)', { size: 5.4, anchor: 'middle', color: INK2, italic: true });
  F.anchor('B.out', R, M(bx + bw), M(ry + 10.7));

  // ---------------- C: row-level scoring (light card)
  const cx0 = 109, cwid = 68;
  L.rect(R, M(cx0), M(ay), M(cwid), M(ah), { fill: '#f7f7f5', stroke: RULE, width: 0.6, r: M(2.2) });
  T(cx0 + 3.5, ay + 5.2, 'Row-level scoring', { size: 7, weight: 'bold' });
  F.anchor('C.in', R, M(cx0), M(ry + 10.7));
  // table
  const tx = cx0 + 3, tw = cwid - 6, ty = ay + 8.6, hh = 5.0, rh2 = 5.6;
  const cols = [['row', 6], ['condition', 16], ['sql', 9], ['wk', 9], ['category', 13.5], ['answer', 8.5]];
  const xs = [];
  let acc = tx;
  cols.forEach(([, w]) => { xs.push(acc); acc += w; });
  L.rect(R, M(tx), M(ty), M(tw), M(hh + 4 * rh2), { fill: '#ffffff', stroke: RULE, width: 0.5, r: M(1) });
  cols.forEach(([h, w], j) => T(xs[j] + w / 2, ty + hh / 2, h, { size: 5.6, weight: 'bold', anchor: 'middle', valign: 'middle', color: INK2 }));
  L.line(R, [M(tx), M(ty + hh)], [M(tx + tw), M(ty + hh)], { stroke: RULE, width: 0.5 });
  const rows = [
    ['1', 'U OR T = T', 'kept', 'dropped', ['absorbing', ABS], false],
    ['2', 'U OR F = U', 'dropped', 'dropped', ['unknown', UNK], true],
    ['3', 'T OR F = T', 'kept', 'kept', ['classical', CLS], true],
    ['4', 'F OR F = F', 'dropped', 'dropped', ['classical', CLS], true],
  ];
  rows.forEach((r, i) => {
    const y0 = ty + hh + i * rh2, ym = y0 + rh2 / 2;
    if (i === 0) L.rect(R, M(tx + 0.25), M(y0 + 0.2), M(tw - 0.5), M(rh2 - 0.4), { fill: ABS, fillOpacity: 0.08 });
    if (i > 0) L.line(R, [M(tx + 1), M(y0)], [M(tx + tw - 1), M(y0)], { stroke: '#e6e5e1', width: 0.4 });
    T(xs[0] + cols[0][1] / 2, ym, r[0], { size: 6, anchor: 'middle', valign: 'middle' });
    T(xs[1] + cols[1][1] / 2, ym, r[1], { size: 6, family: MONO, anchor: 'middle', valign: 'middle' });
    T(xs[2] + cols[2][1] / 2, ym, r[2], { size: 6, anchor: 'middle', valign: 'middle' });
    T(xs[3] + cols[3][1] / 2, ym, r[3], { size: 6, anchor: 'middle', valign: 'middle', weight: i === 0 ? 'bold' : 'normal' });
    const [lab, col] = r[4], chipW = cols[4][1] - 1.6;
    L.rect(R, M(xs[4] + 0.8), M(ym - 1.9), M(chipW), M(3.8), { fill: col, fillOpacity: 0.16, stroke: col, width: 0.6, r: M(1.9) });
    T(xs[4] + cols[4][1] / 2, ym, lab, { size: 5.6, anchor: 'middle', valign: 'middle' });
    // tick / cross drawn as strokes (font glyphs fall back to Type 3 fonts in the PDF)
    const mx = M(xs[5] + cols[5][1] / 2), my = M(ym), k = M(1.25);
    if (r[5]) L.polyline(R, [[mx - k, my], [mx - k * 0.25, my + k * 0.8], [mx + k * 1.1, my - k * 0.95]],
                         { stroke: GOOD, width: 1.2, cap: 'round', join: 'round', fill: 'none' });
    else {
      L.line(R, [mx - k * 0.9, my - k * 0.9], [mx + k * 0.9, my + k * 0.9], { stroke: BAD, width: 1.2, cap: 'round' });
      L.line(R, [mx - k * 0.9, my + k * 0.9], [mx + k * 0.9, my - k * 0.9], { stroke: BAD, width: 1.2, cap: 'round' });
    }
  });
  T(tx, ty + hh + 4 * rh2 + 4.6, 'error rate per category: absorbing vs unknown rows (H1),', { size: 5.6, color: INK2 });
  T(tx, ty + hh + 4 * rh2 + 7.7, 'absorbing vs classical rows of the same items (H2)', { size: 5.6, color: INK2 });

  // ---------------- arrows
  F.relate('A.out', 'B.in', 'flow');
  F.relate('B.pill', 'B.res', 'flow');
  F.relate('B.out', 'C.in', 'flow');
  L.connect(F, 'A.out', 'B.in', { stroke: INK2, width: 0.8, gap0: 1.5, gap1: 1.5 });
  L.connect(F, 'B.pill', 'B.res', { stroke: INK2, width: 0.8, gap0: 1.2, gap1: 1.2 });
  L.connect(F, 'B.out', 'C.in', { stroke: INK2, width: 0.8, gap0: 1.5, gap1: 1.5 });
}

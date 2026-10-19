/* Shorten flat PLM routes while retaining ELK's ports and obstacle avoidance. */
(function () {
const epsilon = 0.01;
const distance = (a, b) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
const bounds = (node, margin = 0) => ({left: node.x - margin, top: node.y - margin,
  right: node.x + node.width + margin, bottom: node.y + node.height + margin});
const overlap = (a, b) => a.left < b.right && a.right > b.left
  && a.top < b.bottom && a.bottom > b.top;

function hits(a, b, box) {
  if (Math.abs(a.y - b.y) < epsilon)
    return a.y > box.top + epsilon && a.y < box.bottom - epsilon
      && Math.max(a.x, b.x) > box.left + epsilon && Math.min(a.x, b.x) < box.right - epsilon;
  if (Math.abs(a.x - b.x) < epsilon)
    return a.x > box.left + epsilon && a.x < box.right - epsilon
      && Math.max(a.y, b.y) > box.top + epsilon && Math.min(a.y, b.y) < box.bottom - epsilon;
  return true;
}

function compact(points) {
  const result = [];
  for (const point of points) {
    if (result.length && distance(result[result.length - 1], point) < epsilon) continue;
    result.push(point);
    while (result.length > 2) {
      const [a, b, c] = result.slice(-3);
      if ((Math.abs(a.x - b.x) < epsilon && Math.abs(b.x - c.x) < epsilon
            && (b.y - a.y) * (c.y - b.y) >= 0)
          || (Math.abs(a.y - b.y) < epsilon && Math.abs(b.y - c.y) < epsilon
            && (b.x - a.x) * (c.x - b.x) >= 0)) result.splice(-2, 1);
      else break;
    }
  }
  return result;
}

function outside(point, node) {
  const sides = [
    {gap: Math.abs(point.x - node.x - node.width), dx: 12, dy: 0},
    {gap: Math.abs(point.x - node.x), dx: -12, dy: 0},
    {gap: Math.abs(point.y - node.y), dx: 0, dy: -12},
    {gap: Math.abs(point.y - node.y - node.height), dx: 0, dy: 12}
  ].sort((a, b) => a.gap - b.gap);
  return {x: point.x + sides[0].dx, y: point.y + sides[0].dy};
}

function length(points) {
  return points.slice(1).reduce((total, point, i) => total + distance(points[i], point), 0);
}

function placeLabels(labels, points, obstacles, occupied) {
  const placed = [];
  const segments = points.slice(1).map((end, i) => ({start: points[i], end}))
    .sort((a, b) => distance(b.start, b.end) - distance(a.start, a.end));
  for (const label of labels) {
    if (!label.width || !label.height) { placed.push({label, x: points[0].x, y: points[0].y}); continue; }
    let found;
    for (const {start, end} of segments) {
      const candidates = [];
      if (Math.abs(start.y - end.y) < epsilon && Math.abs(end.x - start.x) >= label.width + 12) {
        const left = Math.min(start.x, end.x), right = Math.max(start.x, end.x);
        for (const x of [(left + right - label.width) / 2, left + 6, right - label.width - 6])
          for (const y of [start.y - label.height - 6, start.y + 6]) candidates.push({x, y});
      } else if (Math.abs(start.x - end.x) < epsilon && Math.abs(end.y - start.y) >= label.height + 12) {
        const y = (start.y + end.y - label.height) / 2;
        candidates.push({x: start.x + 6, y}, {x: start.x - label.width - 6, y});
      }
      found = candidates.find(({x, y}) => {
        const box = bounds({x, y, width: label.width, height: label.height}, 4);
        return !obstacles.some(other => overlap(box, other))
          && !occupied.some(other => overlap(box, other))
          && !placed.some(other => overlap(box, bounds({...other, width: other.label.width,
            height: other.label.height}, 4)));
      });
      if (found) break;
    }
    if (!found) return null;
    placed.push({label, ...found});
  }
  return placed;
}

window.plmRouteTopology = function (graph) {
  if (graph.children.some(node => node.children && node.children.length)) return graph;
  const nodes = new Map(graph.children.map(node => [node.id, node]));
  const labelsByEdge = new Map(graph.edges.map(edge => [edge,
    (edge.labels || []).filter(label => label.width && label.height).map(label => bounds(label, 4))]));
  const routed = [];
  for (const edge of graph.edges) {
    if (!edge.sections || edge.sections.length !== 1) continue;
    const source = nodes.get(edge.sources[0]), target = nodes.get(edge.targets[0]);
    if (!source || !target) continue;
    const section = edge.sections[0], start = section.startPoint, end = section.endPoint;
    const exit = outside(start, source), entry = outside(end, target);
    const original = compact([start, ...(section.bendPoints || []), end]);
    const obstacles = graph.children.map(node => bounds(node, node === source || node === target ? 0 : 6));
    const occupied = Array.from(labelsByEdge).filter(([owner]) => owner !== edge).flatMap(([, boxes]) => boxes);
    const candidates = [original, [start, end], [start, exit, {x: entry.x, y: exit.y}, entry, end],
      [start, exit, {x: exit.x, y: entry.y}, entry, end]];
    const channels = (values, middle) => Array.from(new Set(values)).sort((a, b) =>
      Math.abs(a - middle) - Math.abs(b - middle)).slice(0, 32);
    const xs = channels([exit.x, entry.x, (exit.x + entry.x) / 2,
      ...graph.children.flatMap(node => [node.x - 12, node.x - 20, node.x + node.width + 12,
        node.x + node.width + 20]), ...(section.bendPoints || []).map(point => point.x)], (exit.x + entry.x) / 2);
    const ys = channels([exit.y, entry.y, (exit.y + entry.y) / 2,
      ...graph.children.flatMap(node => [node.y - 12, node.y + node.height + 12]),
      ...(section.bendPoints || []).map(point => point.y)], (exit.y + entry.y) / 2);
    for (const x of xs) candidates.push([start, exit, {x, y: exit.y}, {x, y: entry.y}, entry, end]);
    for (const y of ys) candidates.push([start, exit, {x: exit.x, y}, {x: entry.x, y}, entry, end]);
    const baseCost = length(original) + Math.max(0, original.length - 2) * 24;
    let best = {points: original, cost: baseCost, labels: null};
    for (const candidate of candidates) {
      const points = compact(candidate);
      const cost = length(points) + Math.max(0, points.length - 2) * 24;
      if (cost >= best.cost - epsilon) continue;
      if (points.slice(1).some((point, i) => obstacles.some(box => hits(points[i], point, box)))) continue;
      const placed = placeLabels(edge.labels || [], points, obstacles, occupied);
      if (!placed) continue;
      // Avoid introducing shared wire runs between unrelated sources.
      let congestion = 0;
      for (const other of routed) {
        if (other.source === edge.sources[0]) continue;
        for (let i = 1; i < points.length; i++) {
          const a = points[i - 1], b = points[i];
          for (let j = 1; j < other.points.length; j++) {
            const c = other.points[j - 1], d = other.points[j];
            if (Math.abs(a.y - b.y) < epsilon && Math.abs(c.y - d.y) < epsilon && Math.abs(a.y - c.y) < epsilon)
              congestion += Math.max(0, Math.min(Math.max(a.x, b.x), Math.max(c.x, d.x))
                - Math.max(Math.min(a.x, b.x), Math.min(c.x, d.x)));
            if (Math.abs(a.x - b.x) < epsilon && Math.abs(c.x - d.x) < epsilon && Math.abs(a.x - c.x) < epsilon)
              congestion += Math.max(0, Math.min(Math.max(a.y, b.y), Math.max(c.y, d.y))
                - Math.max(Math.min(a.y, b.y), Math.min(c.y, d.y)));
          }
        }
      }
      if (cost + congestion * 2 >= best.cost - epsilon) continue;
      best = {points, cost: cost + congestion * 2, labels: placed};
    }
    if (best.labels) {
      section.bendPoints = best.points.slice(1, -1);
      for (const {label, x, y} of best.labels) Object.assign(label, {x, y});
      labelsByEdge.set(edge, best.labels.map(({label, x, y}) => bounds({x, y,
        width: label.width, height: label.height}, 4)));
    }
    routed.push({source: edge.sources[0], points: best.points});
  }
  return graph;
};
})();

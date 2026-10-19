/* Keep PLM endpoint order without moving already-routed SVG elements. */
(function () {
const numberedPrefixes = ['ctrl_', 'phy_', 'phy_pclk_source_', 'phy_rx_source_'];

function prepare(graph) {
  Object.assign(graph.layoutOptions, {
    'elk.algorithm': 'layered',
    'org.eclipse.elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
    'org.eclipse.elk.layered.crossingMinimization.forceNodeModelOrder': true,
    'elk.separateConnectedComponents': false,
    'elk.partitioning.activate': true
  });
  const controllers = new Set(graph.children.filter(node => /^ctrl_[0-9]+$/.test(node.id)
    || node.id === 'dpr').map(node => node.id));
  const outgoing = new Map();
  for (const edge of graph.edges) {
    for (const source of edge.sources) {
      if (!outgoing.has(source)) outgoing.set(source, []);
      outgoing.get(source).push(...edge.targets);
    }
  }
  const downstream = new Set(), pending = Array.from(controllers);
  for (let i = 0; i < pending.length; i++) {
    for (const target of outgoing.get(pending[i]) || []) {
      if (!controllers.has(target) && !downstream.has(target)) {
        downstream.add(target);
        pending.push(target);
      }
    }
  }
  const phySource = graph.edges.some(edge => edge.sources.some(id => /^phy_[0-9]+$/.test(id)));
  for (const node of graph.children) {
    const partition = /^phy_[0-9]+$/.test(node.id) ? (phySource ? 0 : 2)
      : controllers.has(node.id) ? 1 : downstream.has(node.id) ? 2 : 0;
    node.layoutOptions = Object.assign({}, node.layoutOptions, {
      'elk.partitioning.partition': partition
    });
  }
  for (const prefix of numberedPrefixes) {
    const pattern = new RegExp('^' + prefix + '[0-9]+$');
    const positions = [], nodes = [];
    graph.children.forEach((node, index) => {
      if (pattern.test(node.id)) { positions.push(index); nodes.push(node); }
    });
    nodes.sort((a, b) => +a.id.slice(prefix.length) - +b.id.slice(prefix.length));
    positions.forEach((position, index) => { graph.children[position] = nodes[index]; });
    if (prefix !== 'ctrl_') {
      for (const node of nodes) {
        node.layoutOptions = Object.assign({}, node.layoutOptions, {
          'org.eclipse.elk.layered.layering.layerConstraint':
            prefix === 'phy_' && !phySource ? 'LAST' : 'FIRST'
        });
      }
    }
  }
  return graph;
}

function endpointGroups(graph) {
  return numberedPrefixes.map(prefix => graph.children
    .filter(node => new RegExp('^' + prefix + '[0-9]+$').test(node.id))
    .sort((a, b) => +a.id.slice(prefix.length) - +b.id.slice(prefix.length)));
}

function ordered(groups) {
  return groups.every(nodes => nodes.every((node, index) => index === 0
    || nodes[index - 1].y + nodes[index - 1].height <= node.y + 0.5));
}

window.plmLayoutTopology = async function (engine, graph) {
  if (!graph.children.some(node => numberedPrefixes.some(prefix => node.id.startsWith(prefix))))
    return engine.layout(graph);
  let result = await engine.layout(prepare(graph));
  const groups = endpointGroups(result);
  if (ordered(groups)) return window.plmRouteTopology(result);
  // A partition can still span several layers. Use the first layout as
  // position hints, align each endpoint group and let ELK route it again.
  // This keeps all connections attached and avoids moving SVGs over wires.
  for (const nodes of groups) {
    if (!nodes.length) continue;
    const column = Math.max(...nodes.map(node => node.x));
    const rows = nodes.map(node => node.y).sort((a, b) => a - b);
    let bottom = -Infinity;
    nodes.forEach((node, index) => {
      node.x = column;
      node.y = Math.max(rows[index], bottom + 20);
      bottom = node.y + node.height;
    });
  }
  Object.assign(result.layoutOptions, {
    'elk.interactive': true,
    'org.eclipse.elk.layered.cycleBreaking.strategy': 'INTERACTIVE',
    'org.eclipse.elk.layered.layering.strategy': 'INTERACTIVE',
    'org.eclipse.elk.layered.nodePlacement.strategy': 'INTERACTIVE',
    'org.eclipse.elk.layered.crossingMinimization.semiInteractive': true,
    'org.eclipse.elk.layered.interactiveReferencePoint': 'TOP_LEFT'
  });
  result = await engine.layout(result);
  return window.plmRouteTopology(result);
};
})();

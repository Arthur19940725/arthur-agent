const fs = require("fs");
const path = require("path");

function fail(message) {
  console.error(message);
  process.exit(1);
}

function stableByCount(items, countKey) {
  return items.sort(
    (a, b) =>
      b[countKey] - a[countKey] ||
      a.name.localeCompare(b.name) ||
      a.id.localeCompare(b.id),
  );
}

function main() {
  const [, , inputPath, outputPath] = process.argv;
  if (!inputPath || !outputPath) {
    fail("Usage: node ua-tour-analyze.js <input.json> <output.json>");
  }

  let input;
  try {
    input = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  } catch (error) {
    fail(`Failed to read input JSON: ${error.message}`);
  }

  const { nodes, edges, layers } = input;
  if (!Array.isArray(nodes) || !Array.isArray(edges) || !Array.isArray(layers)) {
    fail("Input must contain nodes, edges, and layers arrays.");
  }

  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  if (nodeMap.size !== nodes.length) {
    fail("Input contains duplicate node IDs.");
  }

  const fanIn = new Map(nodes.map((node) => [node.id, 0]));
  const fanOut = new Map(nodes.map((node) => [node.id, 0]));
  for (const edge of edges) {
    if (fanOut.has(edge.source)) {
      fanOut.set(edge.source, fanOut.get(edge.source) + 1);
    }
    if (fanIn.has(edge.target)) {
      fanIn.set(edge.target, fanIn.get(edge.target) + 1);
    }
  }

  const fanInAll = stableByCount(
    nodes.map((node) => ({
      id: node.id,
      fanIn: fanIn.get(node.id),
      name: node.name,
    })),
    "fanIn",
  );
  const fanOutAll = stableByCount(
    nodes.map((node) => ({
      id: node.id,
      fanOut: fanOut.get(node.id),
      name: node.name,
    })),
    "fanOut",
  );

  const topFanOutCount = Math.max(1, Math.ceil(nodes.length * 0.1));
  const lowFanInCount = Math.max(1, Math.ceil(nodes.length * 0.25));
  const highFanOutIds = new Set(
    fanOutAll.slice(0, topFanOutCount).map((item) => item.id),
  );
  const lowFanInIds = new Set(
    [...fanInAll]
      .sort(
        (a, b) =>
          a.fanIn - b.fanIn ||
          a.name.localeCompare(b.name) ||
          a.id.localeCompare(b.id),
      )
      .slice(0, lowFanInCount)
      .map((item) => item.id),
  );

  const entryFileNames = new Set(
    [
      "index.ts",
      "index.js",
      "main.ts",
      "main.js",
      "app.ts",
      "app.js",
      "server.ts",
      "server.js",
      "server.py",
      "mod.rs",
      "main.go",
      "main.py",
      "main.rs",
      "manage.py",
      "app.py",
      "wsgi.py",
      "asgi.py",
      "run.py",
      "__main__.py",
      "application.java",
      "program.cs",
      "config.ru",
      "index.php",
      "app.swift",
      "application.kt",
      "main.cpp",
      "main.c",
    ].map((name) => name.toLowerCase()),
  );

  const allCandidates = nodes
    .map((node) => {
      let score = 0;
      const normalizedPath = String(node.filePath || node.name || "").replace(/\\/g, "/");
      const fileName = String(node.name || path.posix.basename(normalizedPath)).toLowerCase();
      const depth = normalizedPath.split("/").filter(Boolean).length;

      if (node.type === "file") {
        if (entryFileNames.has(fileName)) score += 3;
        if (depth <= 2) score += 1;
        if (highFanOutIds.has(node.id)) score += 1;
        if (lowFanInIds.has(node.id)) score += 1;
      } else if (node.type === "document") {
        if (normalizedPath.toLowerCase() === "readme.md") score += 5;
        else if (depth === 1 && fileName.endsWith(".md")) score += 2;
      }

      return {
        id: node.id,
        score,
        name: node.name,
        summary: node.summary,
        type: node.type,
      };
    })
    .sort(
      (a, b) =>
        b.score - a.score ||
        a.name.localeCompare(b.name) ||
        a.id.localeCompare(b.id),
    );

  const topCodeEntry = allCandidates.find(
    (candidate) => candidate.type === "file" && candidate.score > 0,
  );
  const traversalEdges = edges.filter(
    (edge) =>
      (edge.type === "imports" || edge.type === "calls") &&
      nodeMap.has(edge.source) &&
      nodeMap.has(edge.target),
  );
  const adjacency = new Map(nodes.map((node) => [node.id, []]));
  for (const edge of traversalEdges) {
    const neighbors = adjacency.get(edge.source);
    if (!neighbors.includes(edge.target)) neighbors.push(edge.target);
  }

  const order = [];
  const depthMap = {};
  const byDepth = {};
  if (topCodeEntry) {
    const queue = [topCodeEntry.id];
    depthMap[topCodeEntry.id] = 0;
    while (queue.length > 0) {
      const current = queue.shift();
      const depth = depthMap[current];
      order.push(current);
      if (!byDepth[depth]) byDepth[depth] = [];
      byDepth[depth].push(current);
      for (const next of adjacency.get(current) || []) {
        if (depthMap[next] === undefined) {
          depthMap[next] = depth + 1;
          queue.push(next);
        }
      }
    }
  }

  const inventoryEntry = (node) => ({
    id: node.id,
    name: node.name,
    type: node.type,
    summary: node.summary,
  });
  const nonCodeFiles = {
    documentation: nodes.filter((node) => node.type === "document").map(inventoryEntry),
    infrastructure: nodes
      .filter((node) => ["service", "pipeline", "resource"].includes(node.type))
      .map(inventoryEntry),
    data: nodes
      .filter((node) => ["table", "schema", "endpoint"].includes(node.type))
      .map(inventoryEntry),
    config: nodes.filter((node) => node.type === "config").map(inventoryEntry),
  };

  const relationSet = new Set(
    traversalEdges.map((edge) => `${edge.type}\u0000${edge.source}\u0000${edge.target}`),
  );
  const mutualAdjacency = new Map(nodes.map((node) => [node.id, new Set()]));
  for (const edge of traversalEdges) {
    const reverse = `${edge.type}\u0000${edge.target}\u0000${edge.source}`;
    if (relationSet.has(reverse)) {
      mutualAdjacency.get(edge.source).add(edge.target);
      mutualAdjacency.get(edge.target).add(edge.source);
    }
  }

  const seen = new Set();
  const rawClusters = [];
  for (const node of nodes) {
    if (seen.has(node.id) || mutualAdjacency.get(node.id).size === 0) continue;
    const component = [];
    const queue = [node.id];
    seen.add(node.id);
    while (queue.length > 0) {
      const current = queue.shift();
      component.push(current);
      for (const next of mutualAdjacency.get(current)) {
        if (!seen.has(next)) {
          seen.add(next);
          queue.push(next);
        }
      }
    }
    rawClusters.push(component);
  }

  const allConnections = new Map(nodes.map((node) => [node.id, new Set()]));
  for (const edge of edges) {
    if (nodeMap.has(edge.source) && nodeMap.has(edge.target)) {
      allConnections.get(edge.source).add(edge.target);
      allConnections.get(edge.target).add(edge.source);
    }
  }

  const clusters = rawClusters
    .map((component) => {
      let cluster = [...component];
      if (cluster.length > 5) {
        cluster = cluster
          .sort(
            (a, b) =>
              mutualAdjacency.get(b).size - mutualAdjacency.get(a).size ||
              a.localeCompare(b),
          )
          .slice(0, 5);
      }
      while (cluster.length < 5) {
        const members = new Set(cluster);
        const candidate = nodes
          .filter((node) => !members.has(node.id))
          .map((node) => ({
            id: node.id,
            connections: [...allConnections.get(node.id)].filter((id) => members.has(id)).length,
          }))
          .filter((item) => item.connections >= 2)
          .sort((a, b) => b.connections - a.connections || a.id.localeCompare(b.id))[0];
        if (!candidate) break;
        cluster.push(candidate.id);
      }
      const members = new Set(cluster);
      const edgeCount = edges.filter(
        (edge) => members.has(edge.source) && members.has(edge.target),
      ).length;
      return { nodes: cluster, edgeCount };
    })
    .filter((cluster) => cluster.nodes.length >= 2)
    .sort(
      (a, b) =>
        b.edgeCount - a.edgeCount ||
        a.nodes.join("|").localeCompare(b.nodes.join("|")),
    )
    .slice(0, 10);

  const nodeSummaryIndex = Object.fromEntries(
    nodes.map((node) => [
      node.id,
      { name: node.name, type: node.type, summary: node.summary },
    ]),
  );

  const result = {
    scriptCompleted: true,
    entryPointCandidates: allCandidates.slice(0, 5).map(({ type, ...candidate }) => candidate),
    fanInRanking: fanInAll.slice(0, 20),
    fanOutRanking: fanOutAll.slice(0, 20),
    bfsTraversal: {
      startNode: topCodeEntry ? topCodeEntry.id : null,
      order,
      depthMap,
      byDepth,
    },
    nonCodeFiles,
    clusters,
    layers: {
      count: layers.length,
      list: layers.map(({ id, name, description }) => ({ id, name, description })),
    },
    nodeSummaryIndex,
    totalNodes: nodes.length,
    totalEdges: edges.length,
  };

  try {
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, `${JSON.stringify(result, null, 2)}\n`, "utf8");
  } catch (error) {
    fail(`Failed to write results JSON: ${error.message}`);
  }
}

try {
  main();
} catch (error) {
  fail(`Fatal analysis error: ${error.stack || error.message}`);
}

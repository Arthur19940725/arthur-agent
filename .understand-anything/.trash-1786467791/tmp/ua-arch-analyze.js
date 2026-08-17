#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

function fatal(message) {
  process.stderr.write(`ua-arch-analyze: ${message}\n`);
  process.exit(1);
}

function readJson(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8').replace(/^\uFEFF/, ''));
  } catch (error) {
    fatal(`cannot read JSON ${filePath}: ${error.message}`);
  }
}

function normalizePath(filePath) {
  return String(filePath).replace(/\\/g, '/').replace(/^\.\//, '');
}

function sortedObject(map) {
  return Object.fromEntries(
    [...map.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, value]) => [key, [...value].sort()]),
  );
}

function increment(map, key, amount = 1) {
  map.set(key, (map.get(key) || 0) + amount);
}

function directorySegments(filePath) {
  const segments = normalizePath(filePath).split('/').filter(Boolean);
  return segments.slice(0, -1);
}

function commonDirectoryPrefix(fileNodes) {
  if (fileNodes.length === 0) return [];
  const directories = fileNodes.map((node) => directorySegments(node.filePath));
  const prefix = [];
  const shortest = Math.min(...directories.map((segments) => segments.length));
  for (let index = 0; index < shortest; index += 1) {
    const candidate = directories[0][index];
    if (!directories.every((segments) => segments[index] === candidate)) break;
    prefix.push(candidate);
  }
  return prefix;
}

function filePattern(filePath) {
  const normalized = normalizePath(filePath);
  const lower = normalized.toLowerCase();
  const base = path.posix.basename(lower);

  if (
    /(^|\/)(__tests__|tests?|specs?)(\/|$)/.test(lower)
    || /(^test_.*\.py$|.*_test\.go$|.*test\.java$|.*_spec\.rb$|.*test\.php$|.*tests\.cs$)/.test(base)
    || /\.(test|spec)\.[^.]+$/.test(base)
  ) return 'test';
  if (/\.d\.ts$/.test(base)) return 'types';
  if (/^(index\.(ts|js)|__init__\.py)$/.test(base)) return 'entry';
  if (base === 'manage.py' && !lower.includes('/')) return 'entry';
  if (/^(wsgi|asgi)\.py$/.test(base)) return 'config';
  if (/^cmd\/[^/]+\/main\.go$/.test(lower)) return 'entry';
  if (/^src\/(main|lib)\.rs$/.test(lower)) return 'entry';
  if (/^(application\.java|program\.cs|config\.ru)$/.test(base)) return 'entry';
  if (/^(cargo\.toml|go\.mod|gemfile|pom\.xml|build\.gradle|composer\.json)$/.test(base)) return 'config';
  if (base === 'dockerfile' || /^docker-compose\..+$/.test(base) || /\.tf(vars)?$/.test(base) || base === 'makefile') return 'infrastructure';
  if (/^\.github\/workflows\//.test(lower) || base === '.gitlab-ci.yml' || base === 'jenkinsfile') return 'ci-cd';
  if (/\.sql$/.test(base)) return 'data';
  if (/\.(graphql|gql|proto)$/.test(base)) return 'types';
  if (/\.(md|rst)$/.test(base)) return 'documentation';
  if (/\.ya?ml$/.test(base) || /\.json$/.test(base) || base.startsWith('.env')) return 'config';
  const extension = path.posix.extname(base).replace(/^\./, '');
  return extension ? `extension:${extension}` : 'other';
}

function groupFiles(fileNodes) {
  const commonPrefix = commonDirectoryPrefix(fileNodes);
  const relativeSegments = fileNodes.map((node) => {
    const full = normalizePath(node.filePath).split('/').filter(Boolean);
    return full.slice(commonPrefix.length);
  });
  const flatProject = relativeSegments.every((segments) => segments.length === 1);
  const groups = new Map();
  const nodeToGroup = new Map();

  fileNodes.forEach((node, index) => {
    const relative = relativeSegments[index];
    const group = flatProject
      ? filePattern(node.filePath)
      : (relative.length > 1 ? relative[0] : 'root');
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(node.id);
    nodeToGroup.set(node.id, group);
  });

  return {
    commonPathPrefix: commonPrefix.length ? `${commonPrefix.join('/')}/` : '',
    flatProject,
    groups,
    nodeToGroup,
  };
}

const directoryPatterns = new Map([
  ['routes', 'api'], ['api', 'api'], ['controllers', 'api'], ['endpoints', 'api'], ['handlers', 'api'],
  ['services', 'service'], ['core', 'service'], ['lib', 'service'], ['domain', 'service'], ['logic', 'service'],
  ['models', 'data'], ['db', 'data'], ['data', 'data'], ['persistence', 'data'], ['repository', 'data'], ['entities', 'data'],
  ['components', 'ui'], ['views', 'ui'], ['pages', 'ui'], ['ui', 'ui'], ['layouts', 'ui'], ['screens', 'ui'],
  ['middleware', 'middleware'], ['plugins', 'middleware'], ['interceptors', 'middleware'], ['guards', 'middleware'],
  ['utils', 'utility'], ['helpers', 'utility'], ['common', 'utility'], ['shared', 'utility'], ['tools', 'utility'],
  ['config', 'config'], ['constants', 'config'], ['env', 'config'], ['settings', 'config'],
  ['__tests__', 'test'], ['test', 'test'], ['tests', 'test'], ['spec', 'test'], ['specs', 'test'],
  ['types', 'types'], ['interfaces', 'types'], ['schemas', 'types'], ['contracts', 'types'], ['dtos', 'types'],
  ['hooks', 'hooks'], ['store', 'state'], ['state', 'state'], ['reducers', 'state'], ['actions', 'state'], ['slices', 'state'],
  ['assets', 'assets'], ['static', 'assets'], ['public', 'assets'], ['migrations', 'data'],
  ['management', 'config'], ['commands', 'config'], ['templatetags', 'utility'], ['signals', 'service'],
  ['serializers', 'api'], ['cmd', 'entry'], ['internal', 'service'], ['pkg', 'utility'], ['dto', 'types'],
  ['request', 'types'], ['response', 'types'], ['entity', 'data'], ['controller', 'api'], ['routers', 'api'],
  ['composables', 'service'], ['blueprints', 'api'], ['mailers', 'service'], ['jobs', 'service'], ['channels', 'service'],
  ['bin', 'entry'], ['docs', 'documentation'], ['documentation', 'documentation'], ['wiki', 'documentation'],
  ['deploy', 'infrastructure'], ['deployment', 'infrastructure'], ['infra', 'infrastructure'], ['infrastructure', 'infrastructure'],
  ['.github', 'ci-cd'], ['.gitlab', 'ci-cd'], ['.circleci', 'ci-cd'], ['k8s', 'infrastructure'],
  ['kubernetes', 'infrastructure'], ['helm', 'infrastructure'], ['charts', 'infrastructure'], ['terraform', 'infrastructure'],
  ['tf', 'infrastructure'], ['docker', 'infrastructure'], ['sql', 'data'], ['database', 'data'], ['schema', 'data'],
]);

function directoryPattern(group) {
  const lower = group.toLowerCase();
  if (directoryPatterns.has(lower)) return directoryPatterns.get(lower);
  if (lower === 'src/main/java') return 'service';
  if (lower === 'src/test/java') return 'test';
  return null;
}

function analyze(input) {
  if (!input || !Array.isArray(input.fileNodes) || !Array.isArray(input.importEdges) || !Array.isArray(input.allEdges)) {
    fatal('input must contain fileNodes, importEdges, and allEdges arrays');
  }
  const fileNodes = input.fileNodes;
  const nodeById = new Map();
  for (const node of fileNodes) {
    if (!node || typeof node.id !== 'string' || typeof node.type !== 'string' || typeof node.filePath !== 'string') {
      fatal('every file node must have string id, type, and filePath fields');
    }
    if (nodeById.has(node.id)) fatal(`duplicate file node id: ${node.id}`);
    nodeById.set(node.id, node);
  }
  for (const edge of [...input.importEdges, ...input.allEdges]) {
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) {
      fatal(`edge is not file-level: ${edge.source} -> ${edge.target} (${edge.type})`);
    }
  }
  if (input.importEdges.some((edge) => edge.type !== 'imports')) {
    fatal('importEdges contains a non-imports edge');
  }

  const grouping = groupFiles(fileNodes);
  const nodeTypeGroups = new Map();
  for (const node of fileNodes) {
    if (!nodeTypeGroups.has(node.type)) nodeTypeGroups.set(node.type, []);
    nodeTypeGroups.get(node.type).push(node.id);
  }

  const fileFanIn = new Map(fileNodes.map((node) => [node.id, 0]));
  const fileFanOut = new Map(fileNodes.map((node) => [node.id, 0]));
  const importAdjacency = new Map(fileNodes.map((node) => [node.id, []]));
  const importsFrom = new Map([...grouping.groups.keys()].map((group) => [group, new Set()]));
  const importedBy = new Map([...grouping.groups.keys()].map((group) => [group, new Set()]));
  const interCounts = new Map();
  const density = new Map([...grouping.groups.keys()].map((group) => [group, { internalEdges: 0, totalEdges: 0 }]));

  for (const edge of input.importEdges) {
    increment(fileFanOut, edge.source);
    increment(fileFanIn, edge.target);
    importAdjacency.get(edge.source).push(edge.target);
    const sourceGroup = grouping.nodeToGroup.get(edge.source);
    const targetGroup = grouping.nodeToGroup.get(edge.target);
    density.get(sourceGroup).totalEdges += 1;
    if (targetGroup !== sourceGroup) density.get(targetGroup).totalEdges += 1;
    if (sourceGroup === targetGroup) {
      density.get(sourceGroup).internalEdges += 1;
    } else {
      importsFrom.get(sourceGroup).add(targetGroup);
      importedBy.get(targetGroup).add(sourceGroup);
      increment(interCounts, `${sourceGroup}\u0000${targetGroup}`);
    }
  }

  const crossCounts = new Map();
  const nonCodeConnections = [];
  for (const edge of input.allEdges) {
    const sourceNode = nodeById.get(edge.source);
    const targetNode = nodeById.get(edge.target);
    increment(crossCounts, `${sourceNode.type}\u0000${targetNode.type}\u0000${edge.type}`);
    if ((sourceNode.type === 'file') !== (targetNode.type === 'file')) {
      nonCodeConnections.push({
        sourceNodeId: edge.source,
        sourceType: sourceNode.type,
        targetNodeId: edge.target,
        targetType: targetNode.type,
        edgeType: edge.type,
      });
    }
  }

  const interGroupImports = [...interCounts.entries()]
    .map(([key, count]) => {
      const [from, to] = key.split('\u0000');
      return { from, to, count };
    })
    .sort((left, right) => left.from.localeCompare(right.from) || left.to.localeCompare(right.to));

  const dependencyDirection = [];
  const pairs = new Set();
  for (const { from, to } of interGroupImports) pairs.add([from, to].sort().join('\u0000'));
  for (const pair of [...pairs].sort()) {
    const [left, right] = pair.split('\u0000');
    const leftToRight = interCounts.get(`${left}\u0000${right}`) || 0;
    const rightToLeft = interCounts.get(`${right}\u0000${left}`) || 0;
    if (leftToRight > rightToLeft) dependencyDirection.push({ dependent: left, dependsOn: right, count: leftToRight });
    if (rightToLeft > leftToRight) dependencyDirection.push({ dependent: right, dependsOn: left, count: rightToLeft });
  }

  const patternMatches = {};
  for (const group of [...grouping.groups.keys()].sort()) {
    const match = directoryPattern(group);
    if (match) patternMatches[group] = match;
  }
  const filePatternMatches = Object.fromEntries(
    [...fileNodes]
      .sort((left, right) => left.id.localeCompare(right.id))
      .map((node) => [node.id, filePattern(node.filePath)]),
  );

  const normalizedFiles = fileNodes.map((node) => normalizePath(node.filePath));
  const lowerFiles = normalizedFiles.map((filePath) => filePath.toLowerCase());
  const infraFiles = normalizedFiles.filter((filePath, index) => {
    const lower = lowerFiles[index];
    const base = path.posix.basename(lower);
    return base === 'dockerfile'
      || /^dockerfile\./.test(base)
      || /^docker-compose\./.test(base)
      || /(^|\/)(k8s|kubernetes|helm|charts|terraform|tf|deploy|deployment|infra|infrastructure)(\/|$)/.test(lower)
      || /\.tf(vars)?$/.test(base)
      || /^\.github\/workflows\//.test(lower)
      || base === '.gitlab-ci.yml'
      || base === 'jenkinsfile';
  }).sort();
  const deploymentTopology = {
    hasDockerfile: lowerFiles.some((filePath) => /^dockerfile(?:\.|$)/.test(path.posix.basename(filePath))),
    hasCompose: lowerFiles.some((filePath) => /^docker-compose\.(ya?ml)$/.test(path.posix.basename(filePath))),
    hasK8s: lowerFiles.some((filePath) => /(^|\/)(k8s|kubernetes|helm|charts)(\/|$)/.test(filePath)),
    hasTerraform: lowerFiles.some((filePath) => /(^|\/)(terraform|tf)(\/|$)/.test(filePath) || /\.tf(vars)?$/.test(filePath)),
    hasCI: lowerFiles.some((filePath) => /^\.github\/workflows\//.test(filePath) || /(^|\/)\.gitlab-ci\.yml$/.test(filePath) || /(^|\/)jenkinsfile$/.test(filePath)),
    infraFiles,
  };

  const hasTag = (node, pattern) => (Array.isArray(node.tags) ? node.tags : []).some((tag) => pattern.test(String(tag).toLowerCase()));
  const schemaFiles = fileNodes.filter((node) => {
    const lower = normalizePath(node.filePath).toLowerCase();
    return ['schema', 'table'].includes(node.type) || /\.(sql|graphql|gql|proto|prisma)$/.test(lower);
  }).map((node) => normalizePath(node.filePath)).sort();
  const migrationFiles = fileNodes.filter((node) => /(^|\/)migrations?\//i.test(normalizePath(node.filePath))).map((node) => normalizePath(node.filePath)).sort();
  const dataModelFiles = fileNodes.filter((node) => {
    const lower = normalizePath(node.filePath).toLowerCase();
    return node.type === 'table'
      || /(^|\/)(models?|db|data|persistence|repository|entities)(\/|$)/.test(lower)
      || /(^|\/)(database|db|models?)\.py$/.test(lower)
      || hasTag(node, /^(data-model|database|mysql|orm)$/);
  }).map((node) => normalizePath(node.filePath)).sort();
  const apiHandlerFiles = fileNodes.filter((node) => node.type === 'endpoint' || hasTag(node, /^(api-handler|routing|endpoint)$/)).map((node) => normalizePath(node.filePath)).sort();

  const allGroups = [...grouping.groups.keys()].sort();
  const groupsWithDocs = new Set();
  for (const node of fileNodes) {
    if (node.type === 'document' || /\.(md|rst)$/i.test(node.filePath)) groupsWithDocs.add(grouping.nodeToGroup.get(node.id));
  }
  for (const edge of input.allEdges) {
    if (edge.type === 'documents') groupsWithDocs.add(grouping.nodeToGroup.get(edge.target));
  }
  const undocumentedGroups = allGroups.filter((group) => !groupsWithDocs.has(group));

  const groupAdjacency = Object.fromEntries(allGroups.map((group) => [group, {
    importsFrom: [...importsFrom.get(group)].sort(),
    importedBy: [...importedBy.get(group)].sort(),
  }]));
  const intraGroupDensity = Object.fromEntries(allGroups.map((group) => {
    const stats = density.get(group);
    return [group, {
      internalEdges: stats.internalEdges,
      totalEdges: stats.totalEdges,
      density: stats.totalEdges === 0 ? 0 : Number((stats.internalEdges / stats.totalEdges).toFixed(4)),
    }];
  }));
  const filesPerGroup = Object.fromEntries(allGroups.map((group) => [group, grouping.groups.get(group).length]));
  const nodeTypeCounts = Object.fromEntries([...nodeTypeGroups.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([type, ids]) => [type, ids.length]));

  return {
    scriptCompleted: true,
    commonPathPrefix: grouping.commonPathPrefix,
    flatProject: grouping.flatProject,
    directoryGroups: sortedObject(grouping.groups),
    nodeTypeGroups: sortedObject(nodeTypeGroups),
    importAdjacency: Object.fromEntries([...importAdjacency.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([id, targets]) => [id, targets.sort()])),
    groupAdjacency,
    crossCategoryEdges: [...crossCounts.entries()].map(([key, count]) => {
      const [fromType, toType, edgeType] = key.split('\u0000');
      return { fromType, toType, edgeType, count };
    }).sort((left, right) => left.fromType.localeCompare(right.fromType) || left.toType.localeCompare(right.toType) || left.edgeType.localeCompare(right.edgeType)),
    nonCodeConnections: nonCodeConnections.sort((left, right) => left.sourceNodeId.localeCompare(right.sourceNodeId) || left.targetNodeId.localeCompare(right.targetNodeId) || left.edgeType.localeCompare(right.edgeType)),
    interGroupImports,
    intraGroupDensity,
    patternMatches,
    filePatternMatches,
    deploymentTopology,
    dataPipeline: { schemaFiles, migrationFiles, dataModelFiles, apiHandlerFiles },
    docCoverage: {
      groupsWithDocs: groupsWithDocs.size,
      totalGroups: allGroups.length,
      coverageRatio: allGroups.length === 0 ? 0 : Number((groupsWithDocs.size / allGroups.length).toFixed(4)),
      undocumentedGroups,
    },
    dependencyDirection,
    fileStats: { totalFileNodes: fileNodes.length, filesPerGroup, nodeTypeCounts },
    fileFanIn: Object.fromEntries([...fileFanIn.entries()].sort(([left], [right]) => left.localeCompare(right))),
    fileFanOut: Object.fromEntries([...fileFanOut.entries()].sort(([left], [right]) => left.localeCompare(right))),
  };
}

if (process.argv.length !== 4) {
  fatal('usage: node ua-arch-analyze.js <input.json> <output.json>');
}

const inputPath = path.resolve(process.argv[2]);
const outputPath = path.resolve(process.argv[3]);
const result = analyze(readJson(inputPath));
try {
  fs.writeFileSync(outputPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
} catch (error) {
  fatal(`cannot write JSON ${outputPath}: ${error.message}`);
}

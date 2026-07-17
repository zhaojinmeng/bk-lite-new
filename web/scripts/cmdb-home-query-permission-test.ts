import assert from 'node:assert/strict';
import fs from 'node:fs';

const apiSource = fs.readFileSync('src/app/cmdb/api/changeRecord.ts', 'utf8');
const pageSource = fs.readFileSync('src/app/cmdb/(pages)/assetSearch/page.tsx', 'utf8');
const landingSource = fs.readFileSync('src/app/cmdb/(pages)/assetSearch/landing.tsx', 'utf8');

assert.match(
  apiSource,
  /const getHomeRecentChanges = \(params\?: any\) =>\s*get\('\/cmdb\/api\/change_record\/home_recent\/'/,
  'changeRecord API 必须提供首页专用最近变更方法'
);
assert.match(
  pageSource,
  /const \{ getHomeRecentChanges \} = useChangeRecordApi\(\)/,
  'CMDB 首页必须解构首页专用方法'
);
assert.match(
  pageSource,
  /getHomeRecentChanges\(\s*buildRecentChangeQuery/,
  '首页最近变更加载必须调用专用入口'
);
assert.doesNotMatch(
  pageSource,
  /getChangeRecords\(\s*buildRecentChangeQuery/,
  'CMDB 首页不得继续通过通用操作日志接口加载最近变更'
);
assert.match(
  pageSource,
  /onViewAllChanges=\{canViewOperationLog \? \(\(\) => router\.push\('\/cmdb\/assetManage\/operationLog'\)\) : undefined\}/,
  '首页只应向有操作日志权限的用户提供查看全部入口'
);
assert.match(
  landingSource,
  /\{onViewAllChanges && \(\s*<Button[\s\S]*?onClick=\{onViewAllChanges\}[\s\S]*?<\/Button>\s*\)\}/,
  '最近变更卡片必须在回调缺失时隐藏查看全部按钮'
);

console.log('CMDB 首页查询权限接线测试通过');

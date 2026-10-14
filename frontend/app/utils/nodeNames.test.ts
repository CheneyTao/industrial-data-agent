import { describe, expect, it } from 'vitest';
import { normalizeNodeName } from './nodeNames';
import { groupWorkflowTimeline } from './workflowTimeline';
import { extractReportContent } from './reportTimeline';
import { TextType, type GraphNodeResponse } from '../services/graph/index';

describe('Python and legacy node contract', () => {
  it('normalizes names without changing legacy or unknown names', () => {
    expect(normalizeNodeName('SQL_EXECUTE_NODE')).toBe('SqlExecuteNode');
    expect(normalizeNodeName('REPORT_GENERATOR_NODE')).toBe('ReportGeneratorNode');
    expect(normalizeNodeName('PlannerNode')).toBe('PlannerNode');
    expect(normalizeNodeName('')).toBe('');
  });
  it('renders and extracts historical Python report nodes', () => {
    const report = {nodeName:'REPORT_GENERATOR_NODE', textType:TextType.MARK_DOWN, text:'# Evidence', stepId:'r1'} as GraphNodeResponse;
    expect(groupWorkflowTimeline([[report]])[0]?.nodeName).toBe('ReportGeneratorNode');
    expect(extractReportContent(JSON.stringify([[report]]))).toBe('# Evidence');
  });
});

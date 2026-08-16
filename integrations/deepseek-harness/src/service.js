import { Service } from '@deepseek-ai/cordis'

/**
 * AgentNavi capability seam exposed as ctx.agentNavi.
 *
 * Consumers depend on this interface rather than on the Python CLI directly,
 * so a future daemon, HTTP or MCP provider can replace the local provider.
 */
export class AgentNaviService extends Service {
  constructor(ctx) {
    super(ctx, 'agentNavi')
  }

  async context(_request) {
    throw new Error('agentNavi context provider is not implemented')
  }

  async impact(_request) {
    throw new Error('agentNavi impact provider is not implemented')
  }

  async history(_request) {
    throw new Error('agentNavi history provider is not implemented')
  }

  async scan(_request) {
    throw new Error('agentNavi scan provider is not implemented')
  }

  async ingestHook(_request) {
    throw new Error('agentNavi hook provider is not implemented')
  }

  async startSession(_request) {
    throw new Error('agentNavi session bridge is not implemented')
  }

  async startTurn(_request) {
    throw new Error('agentNavi turn bridge is not implemented')
  }

  async finishTurn(_request) {
    throw new Error('agentNavi turn bridge is not implemented')
  }

  async endSession(_request) {
    throw new Error('agentNavi session bridge is not implemented')
  }

  releaseSession(_sessionId) {}
}

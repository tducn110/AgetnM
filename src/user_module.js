/**
 * User Module - Aggregated Public API Entrypoint
 * Worktree: .agentmesh/worktrees/impl-a
 */

export * from './user_model.js';
export * from './user_service_core.js';
export { status } from './user_service.js';

import { UserService, defaultUserService } from './user_service_core.js';
import { UserRole, UserStatus, validateUserInput, createUserEntity } from './user_model.js';
import { status as harnessStatus } from './user_service.js';

export function createUserModule(customConfig = {}) {
  const service = new UserService();
  return {
    service,
    config: {
      defaultRole: UserRole.MEMBER,
      defaultStatus: UserStatus.ACTIVE,
      ...customConfig,
    },
    version: '1.0.0',
    harnessStatus,
  };
}

export const UserModule = {
  UserService,
  defaultService: defaultUserService,
  UserRole,
  UserStatus,
  validateUserInput,
  createUserEntity,
  createUserModule,
  harnessStatus,
};

export default UserModule;

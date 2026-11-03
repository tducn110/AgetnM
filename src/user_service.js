// Verified Harness Generated Code
export const status = 'OK';

export const UserRole = Object.freeze({
  ADMIN: 'admin',
  MEMBER: 'member',
  GUEST: 'guest',
});

export const UserStatus = Object.freeze({
  ACTIVE: 'active',
  INACTIVE: 'inactive',
  SUSPENDED: 'suspended',
});

export class UserError extends Error {
  constructor(message) { super(message); this.name = 'UserError'; }
}
export class UserNotFoundError extends UserError {
  constructor(id) { super(`User not found: ${id}`); this.name = 'UserNotFoundError'; }
}
export class UserValidationError extends UserError {
  constructor(errors) { super(Array.isArray(errors) ? errors.join('; ') : errors); this.name = 'UserValidationError'; }
}
export class UserDuplicateEmailError extends UserError {
  constructor(email) { super(`Email already registered: ${email}`); this.name = 'UserDuplicateEmailError'; }
}

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const USERNAME_REGEX = /^[a-zA-Z0-9_-]{3,30}$/;

export function validateUserInput(data) {
  const errors = [];
  if (!data || typeof data !== 'object') return { valid: false, errors: ['Input data must be an object'] };
  if (!data.username || !USERNAME_REGEX.test(String(data.username).trim())) errors.push('Username invalid');
  if (!data.email || !EMAIL_REGEX.test(String(data.email).trim())) errors.push('Email invalid');
  return { valid: errors.length === 0, errors };
}

let idCounter = 1;
export function createUserEntity(data) {
  const val = validateUserInput(data);
  if (!val.valid) throw new UserValidationError(val.errors);
  return Object.freeze({
    id: data.id || `usr_${Date.now()}_${idCounter++}`,
    username: data.username.trim(),
    email: data.email.trim().toLowerCase(),
    displayName: data.displayName || data.username.trim(),
    role: data.role || UserRole.MEMBER,
    status: data.status || UserStatus.ACTIVE,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  });
}

export class UserService {
  constructor() { this.users = new Map(); this.emailIndex = new Map(); }
  create(data) {
    const email = (data && data.email) ? String(data.email).trim().toLowerCase() : '';
    if (email && this.emailIndex.has(email)) throw new UserDuplicateEmailError(email);
    const entity = createUserEntity(data);
    this.users.set(entity.id, entity);
    this.emailIndex.set(entity.email, entity.id);
    return entity;
  }
  getById(id) {
    if (!id || !this.users.has(id)) throw new UserNotFoundError(id);
    return this.users.get(id);
  }
  getByEmail(email) {
    const id = this.emailIndex.get(String(email || '').trim().toLowerCase());
    if (!id || !this.users.has(id)) throw new UserNotFoundError(email);
    return this.users.get(id);
  }
  update(id, updates) {
    const existing = this.getById(id);
    if (updates.email && updates.email !== existing.email && this.emailIndex.has(updates.email)) {
      throw new UserDuplicateEmailError(updates.email);
    }
    const updated = Object.freeze({ ...existing, ...updates, updatedAt: new Date().toISOString() });
    this.users.set(id, updated);
    return updated;
  }
  delete(id) {
    const existing = this.getById(id);
    this.emailIndex.delete(existing.email);
    this.users.delete(id);
    return { success: true, deletedId: id };
  }
  list({ page = 1, limit = 20, role, search } = {}) {
    let items = Array.from(this.users.values());
    if (role) items = items.filter(u => u.role === role);
    if (search) items = items.filter(u => u.username.includes(search) || u.email.includes(search));
    const total = items.length;
    const startIndex = (page - 1) * limit;
    return { items: items.slice(startIndex, startIndex + limit), total, page, limit, totalPages: Math.ceil(total / limit) || 1 };
  }
  count() { return this.users.size; }
  clear() { this.users.clear(); this.emailIndex.clear(); }
}

export const defaultUserService = new UserService();

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
    harnessStatus: status,
  };
}

export default defaultUserService;

import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  UserService,
  UserRole,
  UserStatus,
  UserNotFoundError,
  UserValidationError,
  UserDuplicateEmailError,
  createUserModule,
  status as harnessStatus,
} from '../src/user_service.js';

describe('User Module - Unit & Acceptance Suite', () => {
  let service;

  beforeEach(() => {
    service = new UserService();
  });

  test('Harness status contract is OK', () => {
    assert.equal(harnessStatus, 'OK');
  });

  test('Create user with valid data creates entity successfully', () => {
    const user = service.create({
      username: 'alice_smith',
      email: 'alice@example.com',
      displayName: 'Alice Smith',
      role: UserRole.MEMBER,
    });

    assert.ok(user.id.startsWith('usr_'));
    assert.equal(user.username, 'alice_smith');
    assert.equal(user.email, 'alice@example.com');
    assert.equal(user.displayName, 'Alice Smith');
    assert.equal(user.role, 'member');
    assert.equal(user.status, 'active');
    assert.ok(user.createdAt);
    assert.ok(user.updatedAt);
  });

  test('Validation rejects invalid username and email', () => {
    assert.throws(() => {
      service.create({
        username: 'ab', // too short (< 3)
        email: 'invalid-email',
      });
    }, UserValidationError);
  });

  test('Validation rejects duplicate email registrations', () => {
    service.create({
      username: 'user_one',
      email: 'unique@example.com',
    });

    assert.throws(() => {
      service.create({
        username: 'user_two',
        email: 'unique@example.com',
      });
    }, UserDuplicateEmailError);
  });

  test('Get user by ID and email', () => {
    const created = service.create({
      username: 'bob_jones',
      email: 'bob@example.com',
    });

    const foundById = service.getById(created.id);
    assert.equal(foundById.username, 'bob_jones');

    const foundByEmail = service.getByEmail('bob@example.com');
    assert.equal(foundByEmail.id, created.id);
  });

  test('Get non-existent user throws UserNotFoundError', () => {
    assert.throws(() => {
      service.getById('non_existent_id');
    }, UserNotFoundError);

    assert.throws(() => {
      service.getByEmail('missing@example.com');
    }, UserNotFoundError);
  });

  test('Update user updates allowed fields and updatedAt', () => {
    const user = service.create({
      username: 'carol_white',
      email: 'carol@example.com',
    });

    const updated = service.update(user.id, {
      displayName: 'Carol W.',
      role: UserRole.ADMIN,
    });

    assert.equal(updated.displayName, 'Carol W.');
    assert.equal(updated.role, 'admin');
    assert.equal(updated.email, 'carol@example.com');
  });

  test('Delete user removes from store and email index', () => {
    const user = service.create({
      username: 'dave_miller',
      email: 'dave@example.com',
    });

    const res = service.delete(user.id);
    assert.equal(res.success, true);
    assert.equal(service.count(), 0);

    assert.throws(() => {
      service.getById(user.id);
    }, UserNotFoundError);

    // Can register email again after deletion
    const recreated = service.create({
      username: 'dave_new',
      email: 'dave@example.com',
    });
    assert.ok(recreated.id);
  });

  test('List users with pagination, role filter, and search', () => {
    service.create({ username: 'alpha_lead', email: 'alpha@team.com', role: UserRole.ADMIN });
    service.create({ username: 'beta_dev', email: 'beta@team.com', role: UserRole.MEMBER });
    service.create({ username: 'gamma_guest', email: 'gamma@guest.com', role: UserRole.GUEST });

    const adminList = service.list({ role: UserRole.ADMIN });
    assert.equal(adminList.total, 1);
    assert.equal(adminList.items[0].username, 'alpha_lead');

    const searchList = service.list({ search: 'beta' });
    assert.equal(searchList.total, 1);
    assert.equal(searchList.items[0].username, 'beta_dev');

    const paginated = service.list({ page: 1, limit: 2 });
    assert.equal(paginated.items.length, 2);
    assert.equal(paginated.total, 3);
    assert.equal(paginated.totalPages, 2);
  });

  test('Factory function createUserModule produces configured instance', () => {
    const mod = createUserModule({ defaultRole: UserRole.ADMIN });
    assert.ok(mod.service);
    assert.equal(mod.version, '1.0.0');
    assert.equal(mod.config.defaultRole, 'admin');
  });
});

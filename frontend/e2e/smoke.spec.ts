import { test, expect, type Page } from '@playwright/test';
import { writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';

/**
 * Phase 4E: end-to-end smoke test.
 *
 * Exercises the full user flow against a backend running with
 * ``LLM_MOCK=1`` (see playwright.config.ts): register → upload → chat →
 * pagination. The mock LLM emits a fixed three-chunk answer, so we can
 * assert on the assembled text without an OpenAI key.
 *
 * Tests are serialized (workers: 1 in the config) because they share one
 * backend and one scratch data dir.
 */

const uniqueEmail = () => `e2e+${Date.now()}+${Math.floor(Math.random() * 1e6)}@example.com`;

async function register(page: Page, email: string, password = 'test-password-123') {
  await page.goto('/');
  // Auth bootstrap is async; the Auth page mounts once status = 'guest'.
  await page.getByRole('button', { name: '注册' }).click();
  await page.getByLabel('邮箱').fill(email);
  await page.getByLabel('密码 (至少 8 位)').fill(password);
  await page.getByRole('button', { name: '注册并登录' }).click();
  // The auth page disappears once the access token is stored.
  await expect(page).not.toHaveURL(/.*\/auth/);
  // Header chip shows the email once we're authed.
  await expect(page.locator('.auth-user')).toContainText(email);
}

async function uploadCsv(page: Page, filename: string, rows: string) {
  // Home page shows the upload dropzone when no data source is active.
  // Ensure we're on Home.
  await page.getByRole('button', { name: /Chat|聊天/ }).click();
  const input = page.locator('input[type="file"]');
  // Build a temp file Playwright can attach.
  const dir = join(tmpdir(), 'e2e-uploads');
  mkdirSync(dir, { recursive: true });
  const path = join(dir, filename);
  writeFileSync(path, rows, 'utf-8');
  await input.setInputFiles(path);
  // The upload area flips to "uploaded" state showing the filename.
  await expect(page.getByText(filename)).toBeVisible({ timeout: 15_000 });
}

test.describe('Phase 4E smoke', () => {
  test('register → upload → chat → analysis pagination', async ({ page }) => {
    const email = uniqueEmail();
    await register(page, email);

    const csv = 'id,name,amount\n1,alice,100\n2,bob,200\n3,carol,300\n4,dave,400\n5,eve,500\n';
    await uploadCsv(page, 'e2e_sales.csv', csv);

    // Send a chat message and wait for the mock's canned answer.
    const textarea = page.locator('textarea').first();
    await textarea.click();
    await textarea.fill('总结一下这份数据');
    await textarea.press('Enter');

    // The mock streams: "这是 mock 模型的" + "回复。" + "数据看起来没问题。"
    // Assembled: "这是 mock 模型的回复。数据看起来没问题。"
    await expect(page.getByText('mock', { exact: false })).toBeVisible({ timeout: 20_000 });

    // Switch to the Analysis page and confirm the paginated browser loads.
    await page.getByRole('button', { name: /Analysis|分析/ }).click();
    // The schema panel header appears once the table list resolves.
    await expect(page.getByText(/Schema|字段概览/)).toBeVisible({ timeout: 15_000 });
    // Footer "X–Y / 共 N 行" or English equivalent — total should be 5.
    await expect(page.getByText(/5\s*rows|共\s*5\s*行/)).toBeVisible({ timeout: 10_000 });
    // Column headers from the CSV are clickable sort buttons.
    await expect(page.getByRole('columnheader', { name: 'amount' })).toBeVisible();
    await page.getByRole('columnheader', { name: 'amount' }).click();
    // First click sorts asc; the footer should still show 5 rows total.
    await expect(page.getByText(/5\s*rows|共\s*5\s*行/)).toBeVisible();
  });

  test('logout returns to auth page', async ({ page }) => {
    const email = uniqueEmail();
    await register(page, email);
    await page
      .getByRole('button', { name: /退出登录|LogOut/ })
      .first()
      .click();
    // Auth page has a tabbed login/register; assert we're back there.
    await expect(page.getByRole('button', { name: '登录' }).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test('ACL: another user cannot see my data source', async ({ browser }) => {
    // User A registers + uploads.
    const pageA = await browser.newPage();
    const emailA = uniqueEmail();
    await register(pageA, emailA);
    await uploadCsv(pageA, 'acl_secret.csv', 'id,secret\n1,aaa\n2,bbb\n');
    await expect(pageA.getByText('acl_secret.csv')).toBeVisible({ timeout: 15_000 });

    // User B registers in a fresh context — should see zero data sources.
    const pageB = await browser.newPage();
    const emailB = uniqueEmail();
    await register(pageB, emailB);
    // Sidebar's "no data sources" empty state.
    await expect(pageB.getByText(/No data source|没有数据源|尚未/).first()).toBeVisible({
      timeout: 10_000,
    });
    await pageA.close();
    await pageB.close();
  });
});

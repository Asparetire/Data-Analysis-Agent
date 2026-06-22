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
  // Auth.tsx unmounts once status flips to 'authed'; the header chip
  // showing the email is the canonical "we're in" signal.
  await expect(page.locator('.auth-user')).toContainText(email);
}

async function logout(page: Page) {
  await page
    .getByRole('button', { name: /退出登录|LogOut/ })
    .first()
    .click();
  // After logout authStatus flips to 'guest' and Auth.tsx remounts. The
  // "登录" tab button is the canonical signal that we're back at the form.
  await expect(page.getByRole('button', { name: '登录' }).first()).toBeVisible({
    timeout: 10_000,
  });
}

async function login(page: Page, email: string, password = 'test-password-123') {
  // Auth.tsx renders by authStatus state, not URL — there is no /auth route.
  // After logout the Auth form is already mounted on whatever URL we're on,
  // so don't goto('/') (which would re-trigger bootstrap and briefly swap the
  // form for a splash spinner). Just wait for the input to be ready.
  const emailInput = page.getByLabel('邮箱');
  await emailInput.waitFor({ state: 'visible', timeout: 10_000 });
  await emailInput.fill(email);
  await page.getByLabel('密码').fill(password);
  // Submit button text is "登录" — same as the tab button — so target
  // by type=submit to disambiguate.
  await page.locator('button[type="submit"]').click();
  await expect(page.locator('.auth-user')).toContainText(email);
}

async function uploadCsv(page: Page, filename: string, rows: string) {
  // Home page shows the upload dropzone when no data source is active.
  // Ensure we're on Home. Match the nav button exactly so the "清空对话"
  // (clear chat) button on the chat surface doesn't also match.
  // nav.home is "Chat" (en) / "对话" (zh); default locale is zh.
  await page.getByRole('button', { name: '对话', exact: true }).click();
  const input = page.locator('input[type="file"]').first();
  // Build a temp file Playwright can attach.
  const dir = join(tmpdir(), 'e2e-uploads');
  mkdirSync(dir, { recursive: true });
  const path = join(dir, filename);
  writeFileSync(path, rows, 'utf-8');
  await input.setInputFiles(path);
  // The upload area flips to "uploaded" state showing the filename.
  // Use exact match -- the chat header also renders "当前数据源:<filename>",
  // and the sidebar's ds-name title is exactly the filename.
  await expect(page.getByText(filename, { exact: true })).toBeVisible({
    timeout: 15_000,
  });
  // The sidebar's getDataSources refresh (triggered by uploadedFileName
  // changing in the store) runs asynchronously after the upload succeeds.
  // Wait for the data source to appear in the sidebar list before
  // returning, so callers can immediately interact with it (delete,
  // rename, etc.) without racing the refresh.
  await expect(page.locator('.datasource-item', { hasText: filename })).toBeVisible({
    timeout: 15_000,
  });
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
    // Exact match: "打开分析页" (Home's DataPreview button) also matches the
    // regex /分析/. nav.analysis is "Analysis" (en) / "分析" (zh); default zh.
    await page.getByRole('button', { name: '分析', exact: true }).click();
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
    await logout(page);
  });

  test('login as existing user after logout', async ({ page }) => {
    const email = uniqueEmail();
    await register(page, email);
    await logout(page);
    // Now log back in with the same credentials — exercises the login path
    // (register is the only auth flow the other tests hit).
    await login(page, email);
    // Header chip shows the email again.
    await expect(page.locator('.auth-user')).toContainText(email);
  });

  test('wrong password stays on auth page', async ({ page }) => {
    const email = uniqueEmail();
    await register(page, email);
    await logout(page);
    // After logout the Auth form is mounted (authStatus='guest'). Don't
    // goto('/') which would re-trigger bootstrap and briefly hide the form.
    const emailInput = page.getByLabel('邮箱');
    await emailInput.waitFor({ state: 'visible', timeout: 10_000 });
    await emailInput.fill(email);
    await page.getByLabel('密码').fill('totally-wrong-password-xxx');
    await page.locator('button[type="submit"]').click();
    // Login failed — Auth form stays mounted, submit button still visible.
    await expect(page.locator('button[type="submit"]')).toBeVisible({
      timeout: 10_000,
    });
    // No auth-user chip — we never got in.
    await expect(page.locator('.auth-user')).toHaveCount(0);
  });

  test('delete datasource removes it from sidebar', async ({ page }) => {
    const email = uniqueEmail();
    await register(page, email);
    await uploadCsv(page, 'to_delete.csv', 'id,name\n1,x\n');

    // Wait for the sidebar to list the uploaded data source. The upload
    // helper returns once the filename is visible in the Upload component,
    // but the sidebar's getDataSources refresh (Sidebar useEffect on
    // uploadedFileName) runs separately — wait for the ds-name label to
    // appear inside the sidebar list specifically.
    const dsItem = page.locator('.datasource-item', { hasText: 'to_delete.csv' });
    await dsItem.first().waitFor({ state: 'visible', timeout: 15_000 });

    // The delete button uses window.confirm() — a native browser dialog.
    // Playwright auto-dismisses these unless we register a handler.
    page.once('dialog', (d) => void d.accept());

    // Click the delete button by its title attribute (zh="删除",
    // en="Delete") — more robust than CSS class alone, which also matches
    // the attach/lineage buttons.
    const deleteBtn = dsItem
      .first()
      .locator('.ds-row-action[title="删除"], .ds-row-action[title="Delete"]');
    await deleteBtn.click();

    // The data source should disappear from the sidebar list.
    await expect(page.locator('.datasource-item', { hasText: 'to_delete.csv' })).toHaveCount(0, {
      timeout: 10_000,
    });
  });

  test('pagination next page loads more rows', async ({ page }) => {
    const email = uniqueEmail();
    await register(page, email);
    // 25 rows so the default 20-per-page leaves 5 on page 2.
    let csv = 'id,name,amount\n';
    for (let i = 0; i < 25; i++) {
      csv += `${i},user${i},${i * 10}\n`;
    }
    await uploadCsv(page, 'paginated.csv', csv);

    await page.getByRole('button', { name: '分析', exact: true }).click();
    await expect(page.getByText(/Schema|字段概览/)).toBeVisible({ timeout: 15_000 });
    // Footer shows "1–20 / 共 25 行" on page 1.
    await expect(page.getByText(/1[–-]20.*25/)).toBeVisible({ timeout: 10_000 });

    // Click "下一页" / "Next" to advance.
    await page.getByRole('button', { name: /下一页|next/i }).click();
    // Page 2 footer: "21–25 / 共 25 行".
    await expect(page.getByText(/21[–-]25.*25/)).toBeVisible({ timeout: 10_000 });
  });

  test('rename datasource updates sidebar label', async ({ page }) => {
    const email = uniqueEmail();
    await register(page, email);
    await uploadCsv(page, 'rename_me.csv', 'id,name\n1,x\n');

    // Wait for the sidebar to list the uploaded data source.
    const dsItem = page.locator('.datasource-item', { hasText: 'rename_me.csv' });
    await dsItem.first().waitFor({ state: 'visible', timeout: 15_000 });

    // Click the rename button by its title (zh="重命名", en="Rename").
    // Positional nth() is fragile — when the data source is the active
    // source (as it is right after upload), the attach button renders as
    // a non-button <span>, shifting the nth() index of button matches.
    const renameBtn = dsItem
      .first()
      .locator('.ds-row-action[title="重命名"], .ds-row-action[title="Rename"]');
    await renameBtn.click();

    // Inline input with class ds-rename-input appears; fill + Enter to save.
    const input = dsItem.first().locator('.ds-rename-input');
    await input.waitFor({ state: 'visible', timeout: 5_000 });
    await input.fill('我的销售数据');
    await input.press('Enter');

    // The renamed label appears in the sidebar list (span.ds-name inside
    // .datasource-item-main). Scope to the list to avoid matching the
    // current-datasource header which also shows the active source's name.
    await expect(
      page.locator('.datasource-item .ds-name', { hasText: '我的销售数据' }),
    ).toBeVisible({ timeout: 10_000 });
    // Original filename no longer appears as a primary label in the list.
    await expect(
      page.locator('.datasource-item .ds-name', { hasText: 'rename_me.csv' }),
    ).toHaveCount(0);
  });

  test('ACL: another user cannot see my data source', async ({ browser }) => {
    // User A registers + uploads.
    const pageA = await browser.newPage();
    const emailA = uniqueEmail();
    await register(pageA, emailA);
    await uploadCsv(pageA, 'acl_secret.csv', 'id,secret\n1,aaa\n2,bbb\n');
    // Exact match -- the sidebar's ds-name div is exactly the filename; the
    // chat header ("当前数据源:<filename>") and Home preview ("<filename> · 预览
    // N 行") also contain it as a substring.
    await expect(pageA.getByText('acl_secret.csv', { exact: true })).toBeVisible({
      timeout: 15_000,
    });

    // User B registers in a fresh context — should see zero data sources.
    const pageB = await browser.newPage();
    const emailB = uniqueEmail();
    await register(pageB, emailB);
    // Sidebar's "no data sources" empty state. zh: "暂无历史数据源", en: "No data sources yet".
    await expect(pageB.getByText(/No data sources yet|暂无历史数据源/).first()).toBeVisible({
      timeout: 10_000,
    });
    await pageA.close();
    await pageB.close();
  });
});

import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {once} from "node:events";
import {chromium} from "playwright";

const server = spawn(process.env.PYTHON || "python", ["-B","server.py","0"], {stdio:["ignore","pipe","inherit"]});
const lines = createInterface({input:server.stdout});
let browser;
try {
  const [url] = await Promise.race([once(lines,"line"), once(server,"exit").then(([code])=>{throw new Error("server exited: "+code)})]);
  browser = await chromium.launch(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {});
  const context = await browser.newContext({viewport:{width:780,height:620}});
  const [a,b] = await Promise.all([context.newPage(),context.newPage()]);
  await Promise.all([a.goto(url),b.goto(url)]);
  await a.getByLabel("Комментарий к заказу").fill("Принято на складе");
  await b.getByLabel("Комментарий к заказу").fill("Проверить упаковку");
  await a.getByRole("button",{name:"Сохранить",exact:true}).click();
  await a.getByText("Сохранено",{exact:true}).waitFor();
  const conflictResponse = b.waitForResponse(r=>r.request().method()==="PUT" && r.status()===409);
  await b.getByRole("button",{name:"Сохранить",exact:true}).click();
  await conflictResponse;
  await b.getByRole("heading",{name:"Сохранённая версия"}).waitFor();
  assert.equal(await b.getByLabel("Комментарий к заказу").inputValue(),"Проверить упаковку");
  assert.equal((await (await context.request.get(url+"/api/order")).json()).comment,"Принято на складе");
  if (process.env.SCREENSHOT) await b.screenshot({path:process.env.SCREENSHOT});
  await b.getByRole("button",{name:"Сохранить мой вариант"}).click();
  await b.getByText("Сохранено",{exact:true}).waitFor();
  assert.deepEqual(await (await context.request.get(url+"/api/order")).json(),{id:1,version:3,comment:"Проверить упаковку"});
  await a.getByLabel("Комментарий к заказу").fill("Временное изменение");
  await a.getByRole("button",{name:"Сохранить",exact:true}).click();
  await a.getByRole("button",{name:"Взять версию сервера"}).click();
  assert.equal(await a.getByLabel("Комментарий к заказу").inputValue(),"Проверить упаковку");
  console.log("OK: two tabs, HTTP 409, both drafts and both resolution paths.");
} finally {
  await browser?.close();
  server.kill();
  lines.close();
}

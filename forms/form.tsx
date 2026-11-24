import React, {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";

type Order = {version: number; comment: string};
function Editor() {
  const [order, setOrder] = useState<Order | null>(null);
  const [draft, setDraft] = useState("");
  const [conflict, setConflict] = useState<Order | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    fetch("/api/order").then(async response => {
      if (!response.ok) throw new Error();
      const first = await response.json();
      setOrder(first);
      setDraft(first.comment);
    }).catch(() => setMessage("Не удалось загрузить заказ. Обновите страницу."));
  }, []);
  async function save(version: number) {
    setBusy(true);
    try {
      const response = await fetch("/api/order", {method:"PUT", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({version, comment:draft})});
      const result = await response.json();
      if (response.status === 409) {
        setConflict({version:result.server_version, comment:result.server_comment});
        setMessage("Заказ изменился в другой вкладке.");
      } else if (response.ok) {
        setOrder({version:result.new_version, comment:result.saved_comment});
        setConflict(null);
        setMessage("Сохранено");
      } else setMessage("Сохранить не удалось. Черновик остался в поле.");
    } catch { setMessage("Связь прервалась. Черновик остался в поле."); }
    finally { setBusy(false); }
  }
  return <main><h1>Заказ № 1</h1><label htmlFor="draft">Комментарий к заказу</label>
    <textarea id="draft" maxLength={1000} disabled={!order || busy} value={draft} onChange={e=>setDraft(e.target.value)}/>
    <p role="status">{message}</p>
    {conflict ? <section><h2>Сохранённая версия</h2><p>{conflict.comment}</p>
      <button disabled={busy} onClick={()=>save(conflict.version)}>Сохранить мой вариант</button>
      <button disabled={busy} onClick={()=>{setDraft(conflict.comment);setOrder(conflict);setConflict(null);setMessage("Загружена версия сервера");}}>Взять версию сервера</button>
    </section> : <button disabled={!order || busy} onClick={()=>order && save(order.version)}>Сохранить</button>}
  </main>;
}
createRoot(document.getElementById("root")!).render(<Editor/>);

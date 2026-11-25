import React, {useEffect, useState} from "react";
import {createRoot} from "react-dom/client";

type Order = {version: number; comment: string};
type Feedback = {text: string; kind: "notice" | "error" | "success"};

function Editor() {
  const [order, setOrder] = useState<Order | null>(null);
  const [draft, setDraft] = useState("");
  const [conflict, setConflict] = useState<Order | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<Feedback | null>(null);
  useEffect(() => {
    fetch("/api/order").then(async response => {
      if (!response.ok) throw new Error();
      const first = await response.json();
      setOrder(first);
      setDraft(first.comment);
    }).catch(() => setMessage({text: "Не удалось загрузить заказ. Обновите страницу.", kind: "error"}));
  }, []);
  async function save(version: number) {
    setBusy(true);
    try {
      const response = await fetch("/api/order", {method:"PUT", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({version, comment:draft})});
      const result = await response.json();
      if (response.status === 409) {
        setConflict({version:result.server_version, comment:result.server_comment});
        setMessage({text: "Заказ изменился в другой вкладке.", kind: "notice"});
      } else if (response.ok) {
        setOrder({version:result.new_version, comment:result.saved_comment});
        setConflict(null);
        setMessage({text: "Сохранено", kind: "success"});
      } else {
        setMessage({text: "Сохранить не удалось. Черновик остался в поле.", kind: "error"});
      }
    } catch {
      setMessage({text: "Связь прервалась. Черновик остался в поле.", kind: "error"});
    }
    finally { setBusy(false); }
  }
  return (
    <main className="content profile-settings">
      <header className="page-heading card-heading">
        <div className="card-heading-main"><h1 id="order-title">Заказ № 1</h1></div>
      </header>
      <section className="sheet-panel work-panel card-composer" aria-labelledby="order-title">
        <form className="form-stack" onSubmit={event => {
          event.preventDefault();
          if (order && !conflict && !busy) void save(order.version);
        }}>
          <label>Комментарий к заказу
            <textarea id="draft" rows={2} maxLength={1000} disabled={!order || busy} value={draft}
              data-source-role="поле комментария" onChange={event => setDraft(event.target.value)}/>
          </label>
          <p role="status" hidden={!message} className={message ? `feedback ${message.kind} card-local-feedback` : undefined}>
            {message?.text}
          </p>
          {conflict ? (
            <section className="source-confirm" aria-labelledby="conflict-title" data-source-role="выбор версии">
              <h2 id="conflict-title">Сохранённая версия</h2>
              <p className="event-text">{conflict.comment}</p>
              <div className="source-actions">
                <button type="button" className="secondary" disabled={busy} onClick={() => {
                  setDraft(conflict.comment);
                  setOrder(conflict);
                  setConflict(null);
                  setMessage({text: "Загружена версия сервера", kind: "success"});
                }}>Взять версию сервера</button>
                <button type="button" className="secondary" disabled={busy} onClick={() => save(conflict.version)}>
                  Сохранить мой вариант
                </button>
              </div>
            </section>
          ) : (
            <div className="card-save-slot">
              <div className="card-save">
                <button className="go" disabled={!order || busy} data-source-role="сохранение">Сохранить</button>
              </div>
            </div>
          )}
        </form>
      </section>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<Editor/>);

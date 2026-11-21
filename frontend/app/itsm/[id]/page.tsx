import Link from "next/link";

export default async function ItsmStub({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <div className="stub">
      <div className="stub__box">
        <span className="mono">{decodeURIComponent(id)}</span>
        <h1>Здесь откроется инцидент в ITSM</h1>
        <p>
          Это заглушка: приложение только показывает данные и ничего не меняет в ITSM. Чтобы ссылка вела в настоящую систему,
          задайте шаблон адреса в настройках, например <code>{"https://itsm.example/incident/{id}"}</code>.
        </p>
        <Link href="/settings" className="btn">Открыть настройки</Link>
      </div>
    </div>
  );
}

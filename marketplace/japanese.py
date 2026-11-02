"""Standalone Japanese interface pack for FlowBench 1.2.7.

Import this file from the local plugin page. Enable only one language pack
at a time. Disable the pack and restart to fully restore the host language.
User input, protocol identifiers, settings and theme styles are not changed.
"""

from __future__ import annotations

import re
import sys
import weakref


_RESOURCES = [
    ('認可済みの対象を追加：{#0}', '新增授权目标: {#0}', 'Target authorized: {#0}'),
    ('対象の認可を削除：{#0}', '移除授权目标: {#0}', 'Target authorization removed: {#0}'),
    ('認可済みの対象を保存できません：{#0}', '授权目标保存失败: {#0}', 'Failed to save authorized target: {#0}'),
    ('対象の認可の削除を保存できません：{#0}', '授权目标移除保存失败: {#0}', 'Failed to persist target authorization removal: {#0}'),
    ('公開 MQTT リレー（broker.hivemq.com）', '公共 MQTT 中继 (broker.hivemq.com)', 'Public MQTT relay (broker.hivemq.com)'),
    ('{#0}:{#1} に接続できません', '无法连接到 {#0}:{#1}', 'Cannot connect to {#0}:{#1}'),
    ('待ち受けに失敗しました：{#0}', '监听失败: {#0}', 'Listen failed: {#0}'),
    ('参加しました', '已加入', 'Joined'),
    ('公開リレーサーバーに接続中...', '正在连接公共中继服务器...', 'Connecting to public relay server...'),
    ('招待コードが無効、またはルームが満員です', '邀请码无效或已满员', 'Invalid code or room full'),
    ('接続がタイムアウトしました', '连接超时', 'Connection timeout'),
    ('ホストとの接続が切れました', '与主控断开连接', 'Disconnected from host'),
    ('ノード {#0} の接続が切れました', '节点 {#0} 已断开', 'Node {#0} disconnected'),
    ('エラー：paho-mqtt がありません。pip install paho-mqtt を実行してください', '错误：缺少 paho-mqtt 库，请运行 pip install paho-mqtt', 'Error: paho-mqtt missing, run: pip install paho-mqtt'),
    ('リレーの準備完了。招待コード：{#0}（{#1} 経由）', '中继模式已就绪，邀请码 {#0}（通过 {#1}）', 'Relay ready, invite code {#0} (via {#1})'),
    ('リレーへの接続に失敗しました：{#0}', '中继连接失败: {#0}', 'Relay connection failed: {#0}'),
    ('ノード {#0} が参加しました', '节点 {#0} 已加入', 'Node {#0} joined'),
    ('接続に失敗しました：{#0}', '连接失败: {#0}', 'Connection failed: {#0}'),
    ('エラー：paho-mqtt がありません', '错误：缺少 paho-mqtt 库', 'Error: paho-mqtt missing'),
    ('参加しました（リレーモード）', '已加入（中继模式）', 'Joined (relay mode)'),
    ('参加しました（公開リレーモード）', '已加入（公共中继模式）', 'Joined (public relay mode)'),
    ('リレーサーバーとの接続が切れました', '与中继服务器断开连接', 'Disconnected from relay server'),
    ('ルームが満員です', '房间已满', 'Room full'),
    ('招待コードの有効期限が切れました', '邀请码已过期', 'Invite code expired'),
    ('招待コードが無効です', '邀请码无效', 'Invalid invite code'),
    ('参加に失敗しました', '加入失败', 'Join failed'),
    ('参加に失敗しました：{#0}', '加入失败: {#0}', 'Join failed: {#0}'),
    ('ノード {#0} が退出しました', '节点 {#0} 已退出', 'Node {#0} left'),
    ('ホストがルームを閉じました', '主控已关闭房间', 'Host closed the room'),
    ('招待コードが無効、またはルームが満員です', '邀请码无效或房间已满', 'Invalid code or room full'),
    ('接続が拒否されました：{#0}', '连接被拒绝: {#0}', 'Connection rejected: {#0}'),
    ('UPnP ゲートウェイが見つかりません', '未发现 UPnP 网关', 'No UPnP gateway found'),
    ('インポートしましたが読み込めません。プラグインのコードを確認してください', '插件导入但加载失败，请检查插件代码', 'Imported but failed to load; check plugin code'),
    ('プラグインをアンロードしました：{#0}', '插件已卸载：{#0}', 'Plugin unloaded: {#0}'),
    ('プラグインを読み込みました：{#0}', '插件已加载：{#0}', 'Plugin loaded: {#0}'),
    ('元のパスが見つかりません', '源路径不存在', 'Source path not found'),
    ('プラグインのパスが無効です', '无效的插件路径', 'Invalid plugin path'),
    ('プラグインの記録が見つかりません', '插件记录未找到', 'Plugin record not found'),
    ('プラグインをインポートしました：{#0}', '插件已导入：{#0}', 'Plugin imported: {#0}'),
    ('コピーに失敗しました：{#0}', '复制失败：{#0}', 'Copy failed: {#0}'),
    ('プラグインを削除できません：{#0} — {#1}', '插件删除失败：{#0} — {#1}', 'Plugin remove failed: {#0} — {#1}'),
    ('プラグインのメトリクスコールバックに失敗しました：{#0}', '插件指标回调失败: {#0}', 'Plugin metrics callback failed: {#0}'),
    ('フォルダ形式のプラグインには main.py が必要です', '文件夹插件必须包含 main.py', 'Folder plugin must contain main.py'),
    ('.py 形式のプラグインのみ対応しています', '仅支持 .py 插件文件', 'Only .py plugin files supported'),
    ('プラグインの on_test_start に失敗しました：{#0}：{#1}', '插件 on_test_start 回调失败: {#0}: {#1}', 'Plugin on_test_start failed: {#0}: {#1}'),
    ('プラグインの on_test_end に失敗しました：{#0}：{#1}', '插件 on_test_end 回调失败: {#0}: {#1}', 'Plugin on_test_end failed: {#0}: {#1}'),
    ('プラグインの読み込みに失敗しました：{#0} — {#1}', '插件加载失败：{#0} — {#1}', 'Plugin load failed: {#0} — {#1}'),
    ('新しいバージョン {#0} が利用できます', '发现新版本 {#0}', 'New version {#0} available'),
    ('現在のバージョンは v{#0} です。GitHub で {#1} が公開されました。\nダウンロードページを開きますか？', '当前版本 v{#0}，GitHub 已发布 {#1}。\n是否前往下载更新？', 'Current version v{#0}; {#1} is now on GitHub.\nOpen the download page?'),
    ('このバージョンをスキップ（再通知しない）', '跳过此版本（不再提示本次更新）', "Skip this version (don't remind me again)"),
    ('更新の自動確認を無効にする', '不再自动检查更新', "Don't check for updates automatically"),
    ('更新する', '去更新', 'Update'),
    ('後で', '稍后再说', 'Later'),
    ('更新の確認に失敗しました', '检查更新失败', 'Update check failed'),
    ('GitHub に接続できません：{#0}', '无法连接 GitHub：{#0}', 'Cannot reach GitHub: {#0}'),
    ('最新バージョンです', '已是最新版本', 'Up to date'),
    ('最新バージョン（v{#0}）を使用しています。', '当前版本 v{#0}，与 GitHub 最新版本一致。', 'You are on the latest version (v{#0}).'),
    ('FlowBench リレーサーバーノード\n\nこのノードは公開 MQTT リレーに接続します。ポート 8787 を開く必要はありません。\nWindows：windows フォルダを開き、start-agent.cmd を実行してください。\nLinux：linux フォルダを開き、requirements.txt をインストールして ./start-agent.sh を実行してください。\nノードとローカルの FlowBench の両方から broker.hivemq.com に接続できる必要があります。\n所有している対象、またはテストの許可を得た対象にのみ使用してください。', 'FlowBench 中继服务器节点\n\n此节点通过公共 MQTT 中继连接，不需要开放 8787 控制端口。\nWindows：进入 windows 文件夹，双击 start-agent.cmd。\nLinux：进入 linux 文件夹，安装 requirements.txt 后运行 ./start-agent.sh。\n节点和本地 FlowBench 都必须能访问 broker.hivemq.com。\n只对你拥有或取得书面授权的目标执行测试。', 'FlowBench relay server node\n\nThis node connects through the public MQTT relay; port 8787 stays closed.\nWindows: open the windows folder and run start-agent.cmd.\nLinux: open the linux folder, install requirements.txt, then run ./start-agent.sh.\nBoth the node and local FlowBench must be able to reach broker.hivemq.com.\nUse only targets you own or are authorized to test.'),
    ('FlowBench サーバーノード\n\n1. Windows：windows フォルダを開き、agent.json を FlowBench-Agent.exe と同じ場所に置いて start-agent.cmd を実行してください。\n2. Linux：linux フォルダを開き、requests をインストールして ./start-agent.sh を実行してください。\n3. ノードがオンラインになったら、ローカルの負荷テスト画面で設定し、「サーバーで開始」を押してください。\n4. 所有している対象、またはテストの許可を得た対象にのみ使用してください。', 'FlowBench 服务器节点\n\n1. Windows：进入 windows 文件夹，把 agent.json 与 FlowBench-Agent.exe 放在同一目录，双击 start-agent.cmd。\n2. Linux：进入 linux 文件夹，安装 requests 后运行 ./start-agent.sh。\n3. 节点上线后，在本地压力测试页填写配置，再回到服务器节点页点击启动。\n4. 只对你拥有或取得书面授权的目标执行测试。', 'FlowBench server node\n\n1. Windows: open the windows folder, keep agent.json beside FlowBench-Agent.exe, then run start-agent.cmd.\n2. Linux: open the linux folder, install requests, then run ./start-agent.sh.\n3. After the node is online, configure the Stress Test page locally and click Start on server.\n4. Use only targets you own or are authorized to test.'),
    ('サーバーノードの生成', '生成服务器节点', 'Generate Server Node'),
    ('生成', '生成', 'Generate'),
    ('キャンセル', '取消', 'Cancel'),
    ('ノード情報を入力して生成してください。パッケージは下記の場所に保存されます。', '输入节点信息后点击生成。安装包会自动保存到下面的位置。', 'Enter the node information and generate it. The bundle will be saved to the location below.'),
    ('例：server-hk-01', '例如 server-hk-01', 'For example, server-hk-01'),
    ('公開 IP またはドメイン（ポートは省略可能）', '公网 IP 或域名，可带端口', 'Public IP or domain; port optional'),
    ('公開 MQTT リレー経由で接続します。サーバーからインターネットに接続できれば利用でき、ポート 8787 を開く必要はありません。', '通过公共 MQTT 中继连接，服务器只需能够访问外网，无需开放 8787 控制端口。', 'Connect through the public MQTT relay. The server only needs outbound internet access; port 8787 stays closed.'),
    ('参照…', '浏览…', 'Browse…'),
    ('保存先を選択', '选择保存位置', 'Choose Save Location'),
    ('ZIP パッケージ (*.zip)', 'ZIP 安装包 (*.zip)', 'ZIP Bundle (*.zip)'),
    ('ノードを更新', '刷新节点', 'Refresh agents'),
    ('現在の制御チャネル経由でオンラインのサーバーノードを更新します。', '从当前控制通道刷新在线服务器节点列表。', 'Refresh online server agents through the current control channel.'),
    ('サーバーノードを生成', '生成服务器节点', 'Generate server node'),
    ('既存の Hub、一体型 Hub、または公開 MQTT リレーを選択してください。', '可选择现有 Hub、一体化 Hub 或公共 MQTT 中继。', 'Choose an existing Hub, an all-in-one Hub, or the public MQTT relay.'),
    ('更新してノードを取得してください', '先刷新在线节点', 'Refresh to load agents'),
    ('未接続', '未连接', 'Not connected'),
    ('（接続中のノードなし）', '（暂无节点连接）', '(no nodes connected)'),
    ('サーバーで開始', '在服务器启动', 'Start on server'),
    ('サーバーのテストを停止', '停止服务器任务', 'Stop server job'),
    ('ログをコピー', '复制日志', 'Copy Log'),
    ('クリア', '清空', 'Clear'),
    ('（空）', '（暂无）', '(empty)'),
    ('コピーしました', '已复制', 'Copied'),
    ('ログ {#0} 件をコピーしました', '已复制 {#0} 条日志', 'Copied {#0} log entries'),
    ('サーバーノードの操作に失敗しました', '服务器节点操作失败', 'Server-agent operation failed'),
    ('接続方式', '连接方式', 'Connection'),
    ('直接接続 Hub（自前または既存）', 'Hub 直连（自建或现有 Hub）', 'Direct Hub (self-hosted or existing)'),
    ('公開 MQTT リレー（受信制御ポート不要）', '公共 MQTT 中继（免开放控制端口）', 'Public MQTT relay (no inbound control port)'),
    ('ノード名', '节点名称', 'Node name'),
    ('既存の Hub に新しいノードを追加します：{#0}', '将向现有 Hub 追加节点：{#0}', 'A new node will be added to the existing Hub: {#0}'),
    ('Hub が未設定の場合、HTTPS 制御 Hub とノードを自動生成します。サーバーの TCP 8787 を開いてください。', '未配置 Hub 时会自动生成 HTTPS 控制端和节点，服务器需放行 TCP 8787。', 'Without a configured Hub, an HTTPS control Hub and node are generated automatically; open TCP 8787 on the server.'),
    ('サーバーアドレス', '服务器地址', 'Server address'),
    ('保存先', '保存位置', 'Save location'),
    ('サーバーノード', '服务器节点', 'Server Agents'),
    ('サーバーノードは現在の負荷テスト設定を使用します。生成時に自前の Hub または公開 MQTT リレーを選択してください。', '服务器节点使用压力测试页当前配置；生成节点时可选择自建 Hub 或公共 MQTT 中继。', 'Server nodes use the current Stress Test page configuration; choose a self-hosted Hub or the public MQTT relay when generating a node.'),
    ('ノード制御', '节点控制', 'Agent Control'),
    ('制御 URL', '控制地址', 'Control URL'),
    ('制御トークン', '控制令牌', 'Controller token'),
    ('サーバーノード', '服务器节点', 'Server agent'),
    ('ノードの状態', '节点状态', 'Node Status'),
    ('総リクエスト数', '累计请求', 'Total Requests'),
    ('総成功数', '累计成功', 'Total Success'),
    ('現在の QPS', '实时 QPS', 'Live QPS'),
    ('総送信量', '总发送流量', 'Total Sent Traffic'),
    ('ノードの状態と操作結果', '节点状态和操作结果', 'Agent status and operation results'),
    ('制御 URL と制御トークンを入力してください', '请填写控制地址和控制令牌', 'Enter the control URL and controller token'),
    ('ノード名を入力してください', '请填写节点名称', 'Enter a node name'),
    ('保存先を入力してください', '请填写保存位置', 'Enter a save location'),
    ('サーバーの公開 IP またはドメインを入力してください', '请填写服务器公网 IP 或域名', 'Enter the server public IP or domain'),
    ('サーバーアドレスには IP またはドメインを指定してください', '服务器地址必须是 IP 或域名', 'Server address must be an IP or domain'),
    ('ポートは 1～65535 で指定してください', '端口必须在 1–65535', 'Port must be 1-65535'),
    ('サーバー用のファイルが不足しています。アプリを再ビルドしてください。', '安装包缺少服务端文件，请重新构建主程序。', 'The installed app is missing server payload files; rebuild the main app.'),
    ('FlowBench 一体型サーバーノード\n\n制御 URL：{#0}\n\nWindows：解凍して windows フォルダを開き、管理者として start-all.cmd を実行して TCP 8787 を許可してください。\nLinux：解凍して linux フォルダを開き、chmod +x start-all.sh && ./start-all.sh を実行して TCP 8787 を開いてください。スクリプトは Hub の正常性確認後に Agent を起動します。\n起動後、ローカル画面で「ノードを更新」を押してください。\nZIP と同じ場所に .ca.pem 証明書が生成されます。接続に必要なため保管してください。\nこのパッケージのランダムな自己署名証明書は、ご自身の制御環境専用です。\n所有している対象、またはテストの許可を得た対象にのみ使用してください。', 'FlowBench 一体化服务器节点\n\n控制地址：{#0}\n\nWindows：解压后进入 windows，右键以管理员运行 start-all.cmd，并允许防火墙端口 8787。\nLinux 脚本会等待 Hub 健康检查通过后再启动 Agent。\nLinux：解压后进入 linux，执行 chmod +x start-all.sh && ./start-all.sh，并放行 TCP 8787。\n启动完成后，在本地 GUI 服务器节点页点击刷新节点。\n本地会同时生成 .ca.pem 证书文件；不要删除它，GUI 需要它连接控制端。\n这个包使用随机自签名证书，仅用于你自己的服务器控制面。\n只对你拥有或取得书面授权的目标执行测试。', 'FlowBench all-in-one server node\n\nControl URL: {#0}\n\nWindows: extract, open windows, run start-all.cmd as administrator, and allow TCP 8787.\nLinux: extract, open linux, run chmod +x start-all.sh && ./start-all.sh, and open TCP 8787. The script waits for the Hub health check before starting the Agent.\nAfter it starts, click Refresh agents in the local GUI.\nA .ca.pem certificate is generated beside the ZIP; keep it because the GUI needs it to connect.\nThis package uses a random self-signed certificate for your own control plane only.\nUse only targets you own or are authorized to test.'),
    ('ノード用のファイルが不足しています。アプリを再ビルドしてください。', '安装包缺少 Agent 文件，请重新构建主程序。', 'The installed app is missing agent payload files; rebuild the main app.'),
    ('先に負荷テスト画面の対象アドレスを修正してください', '请先修正压力测试页的目标地址', 'Fix the target address on the Stress Test page first'),
    ('先に負荷テスト画面で対象を設定してください', '请先在压力测试页填写目标', 'Set the target on the Stress Test page first'),
    ('高レートの確認', '高请求速率二次确认', 'High Rate Confirmation'),
    ('サーバーノードは負荷テスト設定を使用し、{#0} に最大 {#1} QPS でテストを行います。\n対象への許可と、このレートへの対応能力を確認してください。', '服务器节点将使用压力测试页配置向 {#0} 发送最高 {#1} QPS。\n请再次确认目标已获授权且能够承受该速率。', 'The server agent will use the Stress page configuration against {#0}, up to {#1} QPS.\nConfirm that the target is authorized and can handle this rate.'),
    ('ヘッダーは JSON オブジェクトで指定してください', '请求头必须是 JSON 对象', 'Headers must be a JSON object'),
    ('先に更新してノードを選択してください', '请先刷新并选择节点', 'Refresh and select an agent first'),
    ('先にノードを選択してください', '请先选择节点', 'Select an agent first'),
    ('ノード {#0} 台を読み込みました', '已加载 {#0} 个节点', 'Loaded {#0} agent(s)'),
    ('ノード一覧を更新しました：{#0} 台。', '已刷新节点列表，共 {#0} 个节点。', 'Agent list refreshed: {#0} agent(s).'),
    ('ログなし', '暂无日志', 'No log'),
    ('コピーできるログがありません', '当前没有可复制的日志', 'There is no log to copy'),
    ('対象 {#0} が認可されていないため、サーバーテストをブロックしました', '目标 {#0} 未授权，服务器任务已阻止', 'Target {#0} is not authorized; server job blocked'),
    ('{#0} の認可を保存できません：{#1}', '无法保存目标 {#0} 的授权记录：{#1}', 'Could not save authorization for {#0}: {#1}'),
    ('高レートが確認されていないため、サーバーテストを中止しました', '高速率未确认，服务器任务已取消', 'High rate not confirmed; server job cancelled'),
    ('ヘッダーには有効な JSON を指定してください', '请求头须为合法 JSON', 'Headers must be valid JSON'),
    ('オンライン', '在线', 'online'),
    ('オフライン', '离线', 'offline'),
    ('サーバーノードの状態取得が復旧しました。', '服务器节点状态恢复刷新。', 'Server-agent status polling recovered.'),
    ('ノードパッケージを生成しました：{#0}', '节点安装包已生成：{#0}', 'Node bundle generated: {#0}'),
    ('ノードを生成しました', '节点已生成', 'Node generated'),
    ('解凍して、お使いのサーバーに対応するフォルダをコピーしてください。', '解压后把对应目录复制到服务器即可。', 'Extract it and copy the matching folder to the server.'),
    ('送信済み', '已发送', 'Sent'),
    ('制御チャネルにコマンドを送信しました', '命令已发送到控制通道', 'Command sent to the control channel'),
    ('サーバーノードを選択', '选择服务器节点', 'Select Server Agents'),
    ('選択を適用', '应用选择', 'Apply selection'),
    ('すべて選択', '全选', 'Select all'),
    ('タスクを実行するサーバーを選択', '选择要执行任务的服务器', 'Choose servers for this task'),
    ('1 台以上またはすべてのサーバーを選択できます。オフラインのノードもキューに残り、オンラインになると実行されます。', '可以选择一个、多个或全部服务器。离线节点也可以保留在队列中，待上线后执行。', 'Select one, several, or all servers. Offline nodes can stay queued and run when they come online.'),
    ('サーバー一覧を先に更新してください', '请先刷新服务器节点列表', 'Refresh the server list first'),
    ('複数サーバーを選択…', '多选服务器…', 'Select servers…'),
    ('複数サーバーを選択して同じタスクを実行するパネルを開きます。', '打开面板选择多个服务器执行同一个任务。', 'Open a panel to select multiple servers for the same task.'),
    ('パネルを開き、同じタスクを実行するサーバーを検索して 1 台以上選択します。', '打开面板搜索并选择一个或多个服务器执行同一个任务。', 'Open a panel to search and select one or more servers for the same task.'),
    ('名前、ID、ホスト名、状態で検索（例: hk online）', '搜索名称、ID、主机名或状态（如 hk 在线）', 'Search name, ID, hostname or status (e.g. hk online)'),
    ('現在の検索結果のサーバーをすべて選択', '选中当前搜索结果中的全部服务器', 'Select every server in the current search results'),
    ('現在の検索結果の選択を解除', '取消当前搜索结果中的选择', 'Unselect the servers in the current search results'),
    ('一致するサーバーはありません', '没有匹配的服务器', 'No matching servers'),
    ('不明な OS', '未知系统', 'Unknown OS'),
    ('サーバーノードの選択をクリアしました。', '已清空服务器节点选择。', 'Cleared server-agent selection.'),
    ('サーバーノードを更新して 1 台以上選択してください', '请先刷新并选择服务器节点', 'Refresh and select at least one server agent first'),
    ('サーバーノードを 1 台以上選択してください', '请先选择服务器节点', 'Select at least one server agent first'),
    ('開始', '启动', 'start'),
    ('停止', '停止', 'stop'),
    ('バッチ操作の一部が完了しました', '批量操作部分完成', 'Batch operation partially completed'),
    ('バッチ操作に失敗しました', '批量操作失败', 'Batch operation failed'),
    ('バッチ操作を送信しました', '批量操作已发送', 'Batch operation sent'),
    ('データ待機中', '等待数据', 'waiting for data'),
    ('待機中', '空闲', 'idle'),
    ('準備完了', '就绪', 'ready'),
    ('起動中', '启动中', 'starting'),
    ('実行中', '运行中', 'running'),
    ('停止中', '停止中', 'stopping'),
    ('完了', '完成', 'completed'),
    ('エラー', '错误', 'error'),
    ('サーバーノード {#0} が完了：合計 {#1}  成功 {#2}  失敗 {#3}  送信 {#4}', '服务器节点 {#0} 压测完成：总请求 {#1}  成功 {#2}  失败 {#3}  发送 {#4}', 'Server node {#0} finished: total {#1}  success {#2}  failed {#3}  sent {#4}'),
    ('リレーノードパッケージを生成しました。受信制御ポートは不要です。リレー：{#0}', '中继节点包已生成。服务器无需开放控制端口；中继地址：{#0}', 'Relay node bundle generated. No inbound control port is needed; relay: {#0}'),
    ('一体型パッケージを生成しました。サーバーで start-all を実行してください。ローカルの制御 URL：{#0}', '一体化包已生成。复制到服务器运行 start-all 后，本地控制地址：{#0}', 'All-in-one bundle generated. Run start-all on the server; local control URL: {#0}'),
    ('お待ちください...', '请稍候...', 'Please wait...'),
    ('公開 MQTT リレー（broker.hivemq.com）', '公共 MQTT 中继 (broker.hivemq.com)', 'public MQTT relay (broker.hivemq.com)'),
    ('招待コードを生成', '生成邀请码', 'Generate Invite'),
    ('LAN アドレスをコピー', '复制局域网地址', 'Copy LAN Address'),
    ('管理者として再起動し、ファイアウォールを開く', '以管理员重启并放行防火墙', 'Restart as admin to open firewall'),
    ('開始を一斉送信（負荷テスト設定を使用）', '广播开始（使用压测页配置）', 'Broadcast Start (uses Stress config)'),
    ('停止を一斉送信', '广播停止', 'Broadcast Stop'),
    ('ホストアドレス（LAN IP、ポートは省略可能）', '主控地址（局域网 IP，可带端口）', 'Host address (LAN IP, port optional)'),
    ('192.168.1.100:50505', '192.168.1.100:50505', '192.168.1.100:50505'),
    ('参加', '加入', 'Join'),
    ('退出', '退出', 'Leave'),
    ('共同テストの招待を生成しました', '生成协同邀请码', 'Collab invite generated'),
    ('LAN アドレス：{#0}  ← LAN 内のノード用', '局域网地址：{#0}  ← 内网节点连这个', 'LAN address: {#0}  ← for LAN nodes'),
    ('招待コード {#0} をコピーしました', '邀请码 {#0} 已复制到剪贴板', 'Invite code {#0} copied to clipboard'),
    ('開始を一斉送信：{#0}://{#1}:{#2}', '已广播开始: {#0}://{#1}:{#2}', 'Start broadcast: {#0}://{#1}:{#2}'),
    ('共同テストの開始を一斉送信', '广播协同开始', 'Collab start broadcast'),
    ('接続中...', '正在连接...', 'Connecting...'),
    ('退出しました', '已退出', 'Left'),
    ('ホストのコマンドを受信し、開始します', '收到主控指令，开始压测', 'Received host command; starting'),
    ('負荷テストエンジンがリモート開始要求を拒否しました', '压测引擎拒绝了远程启动请求', 'The stress engine rejected the remote start request'),
    ('ホストのコマンドを受信し、停止します', '收到主控指令，停止压测', 'Received host command; stopping'),
    ('テスト完了：合計 {#0}  成功 {#1}  失敗 {#2}  送信 {#3}', '压测完成：总请求 {#0}  成功 {#1}  失败 {#2}  发送 {#3}', 'Test completed: total {#0}  success {#1}  failed {#2}  sent {#3}'),
    ('共同テストのログ {#0} 件をコピーしました', '已复制 {#0} 条协同日志', 'Copied {#0} collaborative log entries'),
    ('共同テスト', '协同测试', 'Collaborative Testing'),
    ('リレー（WAN）', '中继（外网推荐）', 'Relay (WAN)'),
    ('直接接続（LAN）', '直连（局域网）', 'Direct (LAN)'),
    ('セッションを作成', '发起协同', 'Host a Session'),
    ('最大ノード数', '最大节点数', 'Max Nodes'),
    ('セッションに参加', '加入协同', 'Join a Session'),
    ('招待コード', '邀请码', 'Invite Code'),
    ('💡 招待コードは生成後 5 分間有効です。期限内に参加してください', '💡 邀请码生成后 5 分钟内有效，请在有效期内加入', '💡 Invite code is valid for 5 minutes after generation; join within that window'),
    ('ノード名', '节点名称', 'Node Name'),
    ('共同テストのログ', '协同日志', 'Collab Log'),
    ('リレーモード：{#0}経由で接続します。外部ネットワークのノードに対応し、サーバーの設置は不要です。', '中继模式：通过 {#0} 中转，支持外网节点加入，无需部署服务器、无需公网 IP。', '中继模式：通过 {#0} 中转，支持外网节点加入，无需部署服务器、无需公网 IP。'),
    ('リレーモード：公開 MQTT ブローカー経由で接続します。外部ネットワークのノードに対応し、サーバーの設置は不要です。', 'Relay mode: routed via public MQTT broker, WAN nodes supported, no server setup needed.', 'Relay mode: routed via public MQTT broker, WAN nodes supported, no server setup needed.'),
    ('直接接続モード：ノードはホストと同じ LAN に接続してください。', '直连模式：节点需与主控在同一局域网。', 'Direct mode: nodes must be on the same LAN as the host.'),
    ('リレーモード：{#0}経由で自動接続します。招待コードだけで参加できます。', '中继模式：自动通过 {#0} 连接主控，只需输入邀请码，无需填写主控地址。', '中继模式：自动通过 {#0} 连接主控，只需输入邀请码，无需填写主控地址。'),
    ('リレーモード：公開 MQTT ブローカー経由で自動接続します。招待コードだけで参加できます。', 'Relay mode: auto-connect via public MQTT broker, only the invite code is needed.', 'Relay mode: auto-connect via public MQTT broker, only the invite code is needed.'),
    ('直接接続モード：ホストの LAN IP アドレスを入力してください。', '直连模式：请填写主控的局域网 IP 地址。', "Direct mode: enter the host's LAN IP address."),
    ('接続方式を {#0} に切り替えました。招待コードを再生成中...', '连接模式已切换为{#0}，正在按新模式自动重新生成邀请码…', 'Connection mode switched to {#0}; regenerating invite automatically...'),
    ('招待の生成に失敗しました', '生成邀请码失败', 'Invite generation failed'),
    ('共同テスト用のポートを開けません。使用中でないか確認してください。', '无法监听协同端口，请检查端口是否被占用。', 'The collaboration port could not be opened; check whether it is already in use.'),
    ('招待を生成しました：{#0}（{#1} 経由のリレー）', '已生成邀请码 {#0}（中继模式，通过 {#1} 中转）', 'Invite generated: {#0} (relay via {#1})'),
    ('招待を生成しました（LAN 直接接続）', '已生成邀请码（局域网直连模式）', 'Invite generated (LAN direct mode)'),
    ('リレーへの接続がタイムアウトしました。ネットワークを確認して再試行してください', '连接中继服务器超时，请检查网络后重试', 'Relay connection timed out; check your network and retry'),
    ('外部アドレス（UPnP 自動設定）：{#0}  ← 外部ノード用', '外网地址（UPnP 已自动映射）：{#0}  ← 外网节点连这个', 'WAN address (UPnP auto-mapped): {#0}  ← for WAN nodes'),
    ('ファイアウォール：TCP {#0} を許可済み', '防火墙：已放行 TCP {#0}', 'Firewall: TCP {#0} allowed'),
    ('ファイアウォール：TCP {#0} は未許可です。下のボタンで管理者権限を取得してください', '防火墙：尚未放行 TCP {#0}，点击下方按钮以管理员身份重启后自动放行', 'Firewall: TCP {#0} not allowed yet; click the button below to elevate'),
    ('招待コードの有効期限が切れました', '邀请码已失效', 'Invite code expired'),
    ('この招待コードでは新しいノードは参加できません。ノードを追加するには招待を再生成してください。', '该邀请码已过期，新节点无法使用它加入。如需邀请新节点，请重新生成邀请码。', 'This invite code has expired; new nodes can no longer join with it. Generate a new invite to add nodes.'),
    ('オンラインのノードなし', '暂无在线节点', 'No Online Nodes'),
    ('開始を送信する前に、少なくとも 1 台のノードの参加を待ってください。', '请等待至少一个节点加入后再广播开始指令。', 'Wait for at least one node to join before broadcasting a start command.'),
    ('入力が無効です', '参数错误', 'Invalid input'),
    ('先に負荷テスト画面で対象を設定してください', '请先在压测页填写目标', 'Set the target on the Stress page first'),
    ('共同テストは各ノードに最大 {#0} QPS を設定します。オンライン {#1} 台で、同じ対象への合計は約 {#2} QPS です。\n対象への許可と、このレートへの対応能力を再確認してください。', '协同测试将广播开始：每个节点速率上限 {#0} QPS，当前在线 {#1} 个节点（合计约 {#2} QPS 同时压向同一目标）。\n请再次确认您拥有目标授权，且目标可承受该速率。', 'Collab test will broadcast: rate cap {#0} QPS per node, {#1} node(s) online (~{#2} QPS combined against the same target).\nConfirm again that the target is authorized and can handle this rate.'),
    ('ヘッダーが無効です', '请求头格式错误', 'Invalid headers'),
    ('ヘッダーは JSON オブジェクトで指定してください。例：{"User-Agent": "FlowBench"}', '请求头必须是 JSON 对象，例如 {"User-Agent": "FlowBench"}', 'Headers must be a JSON object, for example {"User-Agent": "FlowBench"}'),
    ('招待コードを入力してください', '请填写邀请码', 'Invite code required'),
    ('ホストアドレスを入力してください', '请填写主控地址', 'Host address required'),
    ('招待コード {#0} で参加しました', '已加入，邀请码 {#0}', 'Joined with code {#0}'),
    ('共同テストに参加しました：コード={#0}', '加入协同: code={#0}', 'Joined collab: code={#0}'),
    ('負荷テストを開始中...', '正在启动压测...', 'Starting stress test...'),
    ('ワーカースレッド {#0} 個を作成中', '正在创建 {#0} 个 worker 线程', 'Creating {#0} worker threads'),
    ('ホスト設定にはオブジェクトを指定してください', '主控配置必须是对象', 'Host configuration must be an object'),
    ('このノードでは利用できないプロトコルです：{#0}', '节点不支持协议：{#0}', 'Protocol is not available on this node: {#0}'),
    ('対象アドレスがありません', '缺少目标地址', 'Target address is missing'),
    ('対象アドレスが長すぎます', '目标地址过长', 'Target address is too long'),
    ('対象アドレスが無効です', '目标地址无效', 'Target address is invalid'),
    ('ヘッダーの件数またはサイズが上限を超えています', '请求头数量或大小超出限制', 'Headers exceed the count or size limit'),
    ('開始に失敗しました', '启动失败', 'Start failed'),
    ('コピーできる共同テストのログがありません', '当前没有可复制的协同日志', 'There is no collaborative log to copy'),
    ('リレーサーバーに接続中...', '正在连接中继服务器...', 'Connecting to relay server...'),
    ('お待ちください', '请稍候', 'Please wait'),
    ('外部アドレス（IPv6、ポート転送不要）：{#0}  ← 外部ノード用', '外网地址（IPv6，无需端口映射，直连可用）：{#0}  ← 外网节点连这个', 'WAN address (IPv6, no port mapping needed): {#0}  ← for WAN nodes'),
    ('ファイアウォール設定のため、管理者として再起動を要求しています。', '请求管理员权限重启以放行防火墙。', 'Requesting admin restart to open firewall.'),
    ('管理者権限がありません', '未授权', 'Not elevated'),
    ('UAC が拒否されたため、ファイアウォールを開けませんでした', '您拒绝了 UAC 授权，防火墙未能放行', 'UAC denied; firewall not opened'),
    ('失敗', '失败', 'Failed'),
    ('中止しました', '已取消', 'Cancelled'),
    ('対象 {#0} が認可されていないため、テストをブロックしました', '目标 {#0} 未授权，测试已阻止', 'Target {#0} not authorized; test blocked'),
    ('認可の保存に失敗しました', '授权保存失败', 'Authorization Save Failed'),
    ('高レートが確認されていないため、一斉送信を中止しました', '高速率未确认，广播已取消', 'High rate not confirmed; broadcast cancelled'),
    ('ホストに接続中...', '正在连接主控...', 'Connecting to host...'),
    ('設定の形式が不正です', '配置格式错误', 'Malformed configuration'),
    ('無効なホスト設定を拒否しました：{#0}', '已拒绝无效的主控配置：{#0}', 'Rejected invalid host configuration: {#0}'),
    ('無効な共同テスト開始設定を拒否しました：{#0}', '拒绝无效协同启动配置：{#0}', 'Rejected invalid collab start configuration: {#0}'),
    ('開始コマンドが無効です', '启动指令无效', 'Invalid Start Command'),
    ('パラメーター {#0} は有効な整数ではありません', '参数 {#0} 不是有效整数', 'Parameter {#0} is not a valid integer'),
    ('パラメーター {#0} は許容範囲 {#1}～{#2} 外です', '参数 {#0} 超出允许范围 {#1}–{#2}', 'Parameter {#0} is outside the allowed range {#1}–{#2}'),
    ('停止中...', '正在停止...', 'Stopping...'),
    ('ワーカースレッドの終了を待っています', '等待 worker 线程退出', 'Waiting for worker threads to exit'),
    ('⏱ 招待の残り時間：{#0} 分 {#1} 秒（参加済みノードは接続を維持し、期限後は新規参加できません）', '⏱ 邀请码有效期：{#0}分{#1}秒（已加入节点不受影响，过期后新节点无法加入）', '⏱ Invite code expires in: {#0}m {#1}s (joined nodes stay connected; new nodes cannot join after expiry)'),
    ('⏱ 招待コードは期限切れです（参加済みノードは接続を維持し、新規参加はできません）', '⏱ 邀请码已过期（已加入节点不受影响，新节点无法加入）', '⏱ Invite code expired (joined nodes stay connected; new nodes cannot join)'),
    ('停止を一斉送信しました', '已广播停止', 'Stop broadcast'),
    ('公開 IP：{#0}（IPv4：ルーターで TCP {#1} を転送するか、リレーモードを使用してください）', '公网 IP：{#0}（IPv4，需在路由器转发 TCP {#1} 到本机，或使用中继模式）', 'Public IP: {#0} (IPv4; forward TCP {#1} on router, or use Relay mode)'),
    ('公開 IP を取得できません（リレーモードへの切り替えを検討してください）', '无法探测公网 IP（建议切换到中继模式）', 'Cannot detect public IP (consider switching to Relay mode)'),
    ('ノード {#0} が完了：合計 {#1}  成功 {#2}  失敗 {#3}  送信 {#4}', '节点 {#0} 压测完成：总请求 {#1}  成功 {#2}  失败 {#3}  发送 {#4}', 'Node {#0} finished: total {#1}  success {#2}  failed {#3}  sent {#4}'),
    ('待機中...', '等待中...', 'waiting...'),
    ('CPU 使用率 %', 'CPU %', 'CPU %'),
    ('メモリ使用率 %', '内存 %', 'Memory %'),
    ('認可済み対象のネットワーク負荷テストと性能監視', '合法授权网络压力测试与性能监控工具', 'Authorized Network Stress Testing & Performance Monitoring'),
    ('クイックスタート', '快速开始', 'Quick Start'),
    ('対象を設定 → 許可を確認 → テスト開始', '配置目标 → 确认授权 → 开始测试', 'Configure target → Confirm authorization → Start'),
    ('負荷テストを開始', '开始压力测试', 'Start Stress Test'),
    ('最近のテスト', '最近测试', 'Recent Test'),
    ('テスト履歴はまだありません', '尚无测试记录', 'No test has been run yet'),
    ('負荷テストを表示', '查看压力测试', 'View Stress Test'),
    ('CPU 使用率', 'CPU 使用率', 'CPU Usage'),
    ('メモリ使用率', '内存使用率', 'Memory Usage'),
    ('受信速度', '下行速率', 'Download'),
    ('送信速度', '上行速率', 'Upload'),
    ('システムリソースの推移', '系统资源趋势', 'System Resource Trend'),
    ('現在のテスト', '当前测试', 'Current Test'),
    ('起動中、リアルタイムデータを待っています...', '正在启动，等待实时数据...', 'Starting, waiting for live data...'),
    ('停止中、集計結果を待っています...', '正在停止，等待汇总结果...', 'Stopping, waiting for summary...'),
    ('最新の結果', '最近结果', 'Latest Result'),
    ('完了 · エラー {#0}% · 平均 {#1} ms · P99 {#2} ms', '已完成 · 错误率 {#0}% · 平均 {#1} ms · P99 {#2} ms', 'Completed · {#0}% errors · {#1} ms avg · P99 {#2} ms'),
    ('使用可能 {#0}%', '剩余 {#0}% 可用', '{#0}% available'),
    ('{#0} / {#1} GB  空き {#2} GB', '{#0} / {#1} GB  剩余 {#2} GB', '{#0} / {#1} GB  {#2} GB free'),
    ('対象 {#0} 件 · {#1}% · {#2} QPS · 成功 {#3} / 失敗 {#4}', '{#0} 个目标 · 进度 {#1}% · QPS {#2} · 成功 {#3} / 失败 {#4}', '{#0} target(s) · {#1}% · {#2} QPS · {#3} ok / {#4} failed'),
    ('{#0}% · {#1} QPS · 成功 {#2} / 失敗 {#3}', '进度 {#0}% · QPS {#1} · 成功 {#2} / 失败 {#3}', '{#0}% · {#1} QPS · {#2} ok / {#3} failed'),
    ('免責事項と利用規約', '免责声明与使用条款', 'Disclaimer & Terms of Use'),
    ('上記のすべての規約を読み、同意します', '我已知晓并同意以上全部条款', 'I have read and agree to all terms above'),
    ('同意する', '接受', 'Accept'),
    ('終了', '退出', 'Exit'),
    ('対象の許可確認', '目标授权确认', 'Target Authorization'),
    ('対象：{#0}', '目标：{#0}', 'Target: {#0}'),
    ('この対象を所有しているか、所有者から書面で許可を得ています', '我确认拥有该目标，或已获得目标所有者的书面授权', 'I own this target or have written authorization from its owner'),
    ('無許可の負荷テストが違法であることを理解し、すべての法的責任を負います', '我理解未授权压测属违法行为，并愿意承担全部法律责任', 'I understand unauthorized stress testing is illegal and I accept full liability'),
    ('許可の説明（必須。例：自分のサーバー／契約番号）', '授权说明（必填，如：自有服务器 / 合同编号等）', 'Authorization note (required, e.g. own server / contract no.)'),
    ('確認', '确认授权', 'Confirm'),
    ('ホーム', '主页', 'Home'),
    ('負荷テスト', '压力测试', 'Stress Test'),
    ('共同テスト', '协同测试', 'Collaborative'),
    ('モニター', '监控面板', 'Monitor'),
    ('プラグイン', '插件', 'Plugins'),
    ('設定', '设置', 'Settings'),
    ('ウィンドウを表示', '显示主窗口', 'Show Window'),
    ('終了', '退出', 'Quit'),
    ('トレイに最小化しました。終了するにはトレイのアイコンを右クリックしてください', '程序已最小化到托盘，右键托盘图标可退出', 'Minimized to tray, right-click tray icon to quit'),
    ('プラグインの画面を作成できません：{#0} — {#1}', '插件页面创建失败：{#0} — {#1}', 'Plugin page creation failed: {#0} — {#1}'),
    ('プラグインの画面を登録できません：{#0} — {#1}', '插件页面注册失败：{#0} — {#1}', 'Plugin page registration failed: {#0} — {#1}'),
    ('ツール', '工具', 'Tools'),
    ('プロトコル', '协议', 'Protocols'),
    ('画面・UI', '界面', 'UI & Pages'),
    ('その他', '其他', 'Misc'),
    ('## 新規プラグインの提出\n\n- **プラグイン ID**：{#0}\n- **名前**：{#1} / {#2}\n- **バージョン**：{#3}\n- **作者**：{#4}\n\nFlowBench クライアントから公開しました。', '## 新插件提交\n\n- **插件 ID**: {#0}\n- **名称**: {#1} / {#2}\n- **版本**: {#3}\n- **作者**: {#4}\n\n由 FlowBench 客户端一键发布。', '## New plugin submission\n\n- **Plugin ID**: {#0}\n- **Name**: {#1} / {#2}\n- **Version**: {#3}\n- **Author**: {#4}\n\nPublished from the FlowBench client.'),
    ('公開を取り下げる', '下架', 'Unpublish'),
    ('お気に入りから削除', '取消收藏', 'Remove from favorites'),
    ('お気に入りに追加', '收藏插件', 'Add to favorites'),
    ('お気に入りから削除：{#0}', '取消收藏：{#0}', 'Remove from favorites: {#0}'),
    ('お気に入りに追加：{#0}', '收藏：{#0}', 'Add to favorites: {#0}'),
    ('プラグインの公開を取り下げる', '下架插件', 'Unpublish Plugin'),
    ('プラグイン「{#0}」の公開を取り下げますか？\n\nマーケットの一覧から削除する Pull Request を作成します。PR のマージ後、他のユーザーには表示されなくなります。GitHub の認証が必要です。', '确定要下架插件「{#0}」吗？\n\n将创建一个 Pull Request 从市场索引中移除该插件，PR 合并后插件不再对用户可见。需要 GitHub 授权。', 'Unpublish plugin "{#0}"?\n\nThis will create a Pull Request removing it from the marketplace index. It will no longer be visible after the PR is merged. GitHub authorization required.'),
    ('再読み込み', '重载', 'Reload'),
    ('削除', '删除', 'Remove'),
    ('実行中', '运行中', 'Running'),
    ('無効', '已禁用', 'Disabled'),
    ('読み込み失敗', '加载失败', 'Load failed'),
    ('未読み込み', '未加载', 'Not loaded'),
    ('状態：{#0}', '状态：{#0}', 'State: {#0}'),
    ('プラグインを削除', '删除插件', 'Remove Plugin'),
    ('プラグイン「{#0}」を削除しますか？ファイルも削除されます。', '确定删除插件"{#0}"？插件文件将从磁盘移除。', 'Remove plugin "{#0}"? Its files will be deleted.'),
    ('プラグインはアプリと同じ権限で動く第三者のコードです。信頼できる提供元のものだけをインストールしてください。免責事項も適用されます。', '插件为第三方代码，拥有与主程序相同的权限，请仅安装可信来源的插件；插件行为同样受免责声明约束。', 'Plugins are third-party code with the same privileges as the app. Only install from trusted sources; the disclaimer applies.'),
    ('プラグインをインポート…', '导入插件…', 'Import Plugin…'),
    ('プラグインフォルダを開く', '打开插件目录', 'Open Plugin Folder'),
    ('再スキャン', '重新扫描', 'Rescan'),
    ('プラグインファイルを選択', '选择插件文件', 'Select plugin file'),
    ('Python プラグイン (*.py);;すべてのファイル (*.*)', 'Python 插件 (*.py);;所有文件 (*.*)', 'Python plugin (*.py);;All files (*.*)'),
    ('スキャンが完了しました', '扫描完成', 'Rescan done'),
    ('プラグイン {#0} 個を読み込みました', '共加载 {#0} 个插件', '{#0} plugin(s) loaded'),
    ('名前・作者・説明で検索…', '搜索插件名称、作者、描述…', 'Search by name, author, description…'),
    ('プラグインを検索', '搜索插件', 'Search plugins'),
    ('検索候補を表示', '展开搜索推荐', 'Show search suggestions'),
    ('種類とインストール状態で絞り込み', '筛选类型和安装状态', 'Filter by type and install status'),
    ('お気に入りのみ', '仅看收藏', 'Favorites only'),
    ('すべてのフィルターを解除', '清除全部筛选', 'Clear all filters'),
    ('降順（クリックで切り替え）', '倒序（点击切换）', 'Descending (click to toggle)'),
    ('プラグインを公開…', '发布插件…', 'Publish a Plugin…'),
    ('更新', '刷新', 'Refresh'),
    ('人気の検索', '热门搜索', 'Popular searches'),
    ('検索履歴', '搜索历史', 'Search history'),
    ('すべてクリア', '全部清空', 'Clear all'),
    ('検索履歴をすべて削除', '清空全部搜索历史', 'Clear all search history'),
    ('候補はありません。キーワードを入力して検索してください', '暂无推荐，可直接输入关键词搜索', 'No suggestions yet; type a keyword to search'),
    ('マーケットを読み込み中…', '正在加载市场…', 'Loading marketplace…'),
    ('（オフラインキャッシュ）', '（离线缓存）', ' (offline cache)'),
    ('検索候補を非表示', '收起搜索推荐', 'Hide search suggestions'),
    ('未インストール', '未安装', 'Not installed'),
    ('インストール済み', '已安装', 'Installed'),
    ('更新あり', '可更新', 'Updates available'),
    ('すべての状態', '全部状态', 'All states'),
    ('お気に入りのみ（{#0}）', '仅看收藏 ({#0})', 'Favorites only ({#0})'),
    ('マーケットを読み込めません：{#0}\nネットワークを確認して「更新」を押してください。', '市场加载失败：{#0}\n请检查网络后点击"刷新"。', 'Failed to load marketplace: {#0}\nCheck your network and hit Refresh.'),
    ('公開を取り下げ中…', '下架中…', 'Unpublishing…'),
    ('認証が必要です', '需要授权', 'Authorization required'),
    ('ブラウザーで認証してください（コード：{#0}）。認証後、自動的に取り下げを続行します。', '请在浏览器中授权（代码 {#0}），授权后将自动继续下架。', 'Authorize in browser (code {#0}), unpublish will continue automatically.'),
    ('プラグイン {#0} 個', '共 {#0} 个插件', '{#0} plugin(s) total'),
    ('公開の取り下げに失敗しました', '下架失败', 'Unpublish failed'),
    ('ダウンロードに失敗しました', '下载失败', 'Download failed'),
    ('{#0}: {#1}', '{#0}：{#1}', '{#0}: {#1}'),
    ('マーケットに公開', '发布插件到市场', 'Publish to Marketplace'),
    ('公開方法（無料・サーバー不要）：\n1. ローカルのプラグインとアイコンを選択します。\n2. 初回の「公開」でブラウザーを開き、一度だけ認証します。\n3. 以降はワンクリックで公開できます。PR のマージ後に公開されます。', '上架流程（免费，无需服务器）：\n1. 选择要发布的本地插件和图标；\n2. 首次点击"一键发布"会打开浏览器，点一次"授权"即可；\n3. 之后每次发布只需一键，PR 合并后即上架。', 'How publishing works (free, no server):\n1. Pick the local plugin and icon;\n2. The first "Publish" click opens your browser for a one-time authorization;\n3. After that every publish is one click. It goes live once the PR is merged.'),
    ('アイコンなし', '无图标', 'No icon'),
    ('アイコンを選択（PNG/JPG）…', '选择图标 (PNG/JPG)…', 'Pick Icon (PNG/JPG)…'),
    ('ghp_xxxxxxxxxxxx (public_repo, workflow)', 'ghp_xxxxxxxxxxxx（public_repo, workflow 权限）', 'ghp_xxxxxxxxxxxx (public_repo, workflow)'),
    ('トークンを取得', '获取 Token', 'Get Token'),
    ('ワンクリックで公開', '一键发布', 'Publish (1-Click)'),
    ('JSON をコピー', '复制 JSON', 'Copy JSON'),
    ('手動で提出', '手动提交页面', 'Manual Submission'),
    ('次のコードをブラウザーに入力するか、クリックしてコピーしてください：', '请在浏览器中输入以下授权码，或直接点击复制：', 'Enter this code in your browser, or click to copy:'),
    ('クリックして認証コードをコピー', '点击复制授权码', 'Click to copy code'),
    ('ブラウザーを再度開く', '重新打开浏览器', 'Reopen Browser'),
    ('アイコンは base64 として一覧に埋め込みます（最大 64KB）。SHA-256 で整合性を確認します。トークンはローカルにのみ保存し、PR の作成に使用します。', '图标会以 base64 内嵌进索引（上限 64KB）；sha256 用于完整性校验。Token 仅保存在本地，用于创建 PR。', 'Icon is base64-embedded in the index (max 64KB); sha256 for integrity. Token is stored locally and used only to create the PR.'),
    ('閉じる', '关闭', 'Close'),
    ('アイコンを選択', '选择图标', 'Pick an icon'),
    ('画像 (*.png *.jpg *.jpeg)', '图片 (*.png *.jpg *.jpeg)', 'Images (*.png *.jpg *.jpeg)'),
    ('項目の JSON をクリップボードにコピーしました', '条目 JSON 已复制到剪贴板', 'Entry JSON copied to clipboard'),
    ('認証を要求中…', '正在请求授权…', 'Requesting authorization…'),
    ('GitHub に接続中…', '正在连接 GitHub…', 'Connecting to GitHub…'),
    ('ブラウザーでの認証待ち…', '等待浏览器授权…', 'Waiting for browser…'),
    ('開いたブラウザーで「Authorize」を押してください。開かなかった場合は右の「ブラウザーを再度開く」を押すか、github.com/login/device でコードを入力してください。', '请在打开的浏览器中点击「Authorize」完成授权。若浏览器未自动打开，请点击右侧「重新打开浏览器」，或手动访问 github.com/login/device 并输入代码。', "Click Authorize in the opened browser. If it didn't open, click 'Reopen Browser' on the right or visit github.com/login/device and enter the code."),
    ('認証コード {#0} をコピーしました', '授权码 {#0} 已复制', 'Code {#0} copied'),
    ('公開中…', '发布中…', 'Publishing…'),
    ('リポジトリを Fork 中（{#0}）…', '正在 Fork 仓库（{#0}）…', 'Forking repository ({#0})…'),
    ('プラグインファイルをアップロード中…', '正在上传插件文件…', 'Uploading plugin file…'),
    ('プラグイン一覧を更新中…', '正在更新插件索引…', 'Updating plugin index…'),
    ('提出中、手動での審査を待っています…', '正在提交，等待人工审核…', 'Submitting, awaiting manual review…'),
    ('公開に失敗しました：{#0}', '发布失败：{#0}', 'Publish failed: {#0}'),
    ('公開に失敗しました', '发布失败', 'Publish failed'),
    ('有効にする', '启用', 'Enable'),
    ('有効', '已启用', 'Enabled'),
    ('{#0} を有効にしました', '{#0} 已启用', '{#0} enabled'),
    ('ダウンロード中…', '下载中…', 'Downloading…'),
    ('再読み込みしました', '重载完成', 'Reloaded'),
    ('プラグインを再読み込みしました：{#0}', '插件已重新加载：{#0}', 'Plugin reloaded: {#0}'),
    ('不明なエラー', '未知错误', 'Unknown error'),
    ('再読み込みに失敗しました', '重载失败', 'Reload failed'),
    ('削除しました', '已删除', 'Removed'),
    ('インポートしました', '导入成功', 'Imported'),
    ('プラグインをインポートし、有効にしました', '插件已导入并启用', 'Plugin imported and enabled'),
    ('インポートに失敗しました', '导入失败', 'Import failed'),
    ('日時順', '按时间', 'Time'),
    ('名前', '按名称', 'Name'),
    ('作者', '按作者', 'Author'),
    ('バージョン', '按版本', 'Version'),
    ('状態', '按状态', 'Status'),
    ('この検索履歴を削除', '删除这条历史', 'Delete this search'),
    ('人気の検索 第 {#0} 位：{#1}', '第 {#0} 名热门搜索：{#1}', 'Popular search #{#0}: {#1}'),
    ('検索履歴：{#0}', '搜索历史：{#0}', 'Search history: {#0}'),
    ('検索履歴を削除：{#0}', '删除搜索历史：{#0}', 'Delete search history: {#0}'),
    ('適用中のフィルター：{#0}', '当前筛选：{#0}', 'Active filters: {#0}'),
    ('すべて', '全部', 'All'),
    ('昇順（クリックで切り替え）', '正序（点击切换）', 'Ascending (click to toggle)'),
    ('インストールできるプラグインはありません{#0}{#1}', '暂无可安装的插件{#0}{#1}', 'No installable plugins{#0}{#1}'),
    ('公開を取り下げる権限がありません', '无权下架', 'Unpublish not allowed'),
    ('このプラグインの公開を取り下げられるのは作者のみです。', '只有插件作者可以下架自己的插件。', 'Only the plugin publisher can unpublish this plugin.'),
    ('公開を取り下げました', '下架成功', 'Unpublished'),
    ('プラグイン {#0} の公開を取り下げました。他のユーザーには更新後に表示されなくなります。', '插件 {#0} 已从市场下架，其他用户刷新后不再显示。', 'Plugin {#0} removed from marketplace. It disappears for others after refresh.'),
    ('{#0} の取り下げ要求を提出しました。数秒後に反映されます。', '插件 {#0} 的下架请求已提交，将在几秒内自动生效。', 'Unpublish request for {#0} submitted. It will take effect within seconds.'),
    ('インストール済み', '安装成功', 'Installed'),
    ('{#0} をインストールし、有効にしました', '{#0} 已安装并启用', '{#0} installed and enabled'),
    ('インストールに失敗しました', '安装失败', 'Install failed'),
    ('ローカルのプラグイン', '本地插件', 'Local Plugins'),
    ('マーケット', '插件市场', 'Marketplace'),
    ('ローカルのプラグイン', '本地插件', 'Local plugins'),
    ('分類', '插件分类', 'Category'),
    ('GitHub トークン', 'GitHub Token', 'GitHub Token'),
    ('アイコンが大きすぎます', '图标过大', 'Icon too large'),
    ('最大 64KB、現在 {#0}KB', '上限 64KB，当前 {#0}KB', 'Max 64KB, got {#0}KB'),
    ('公開できません', '无法发布', 'Cannot publish'),
    ('先にローカルのプラグインを選択してください', '请先选择一个本地插件', 'Please select a local plugin first'),
    ('ブラウザーが開きませんでした', '浏览器未打开', "Browser didn't open"),
    ('「ブラウザーを再度開く」を押すか、コードを手動でコピーしてください', '请点击「重新打开浏览器」按钮或手动复制代码', "Click 'Reopen Browser' or copy the code manually"),
    ('プラグインファイルがありません', '插件文件缺失', 'Plugin file missing'),
    ('プラグインのソースファイルが見つかりません', '找不到插件源文件', 'Cannot find plugin source file'),
    ('リポジトリへの書き込み権限を確認しました。直接公開します…', '检测到仓库写权限，直接上架…', 'Write access detected, publishing directly…'),
    ('✓ 直接公開しました：{#0}', '✓ 已直接上架：{#0}', '✓ Published directly: {#0}'),
    ('公開しました', '发布成功', 'Published'),
    ('プラグインを公開しました。他のユーザーはマーケットを更新すると確認できます。', '插件已直接上架，其他用户刷新市场即可看到。', 'Plugin is now live. Other users will see it after refreshing the marketplace.'),
    ('✓ 提出しました。審査待ちです：{#0}', '✓ 已提交，等待审核：{#0}', '✓ Submitted, awaiting review: {#0}'),
    ('プラグインを提出しました。PR の審査とマージ後に公開されます。', '插件已提交，审核通过并合并 PR 后上架。', 'Plugin submitted. It will go live after the PR is reviewed and merged.'),
    ('日本語', '中', 'en'),
    ('プラグインはまだありません。「プラグインをインポート…」で .py ファイルを追加するか、プラグインフォルダに置いて再スキャンしてください。', '暂无插件。点击"导入插件…"添加 .py 插件文件，或将插件放入插件目录后重新扫描。', 'No plugins yet. Use "Import Plugin…" to add a .py file, or drop plugins into the folder and rescan.'),
    ('{#0} に一致するプラグインはありません。検索条件やフィルターを解除してください', '没有找到匹配 {#0} 的插件，可清空关键词或筛选', 'No plugins match {#0}; clear the query or filters'),
    ('先にプラグインを公開して認証してください。公開の取り下げにも GitHub の認証が必要です。', '请先发布一个插件完成授权，下架也需要 GitHub 身份。', 'Publish a plugin first to complete authorization; unpublish also requires GitHub identity.'),
    ('## プラグインの公開取り下げ\n\n- **プラグイン ID**：{#0}\n- **名前**：{#1}\n\nFlowBench マーケットが自動生成しました。', '## 下架插件\n\n- **插件 ID**: {#0}\n- **名称**: {#1}\n\n由 FlowBench 插件市场一键下架功能自动创建。', '## Unpublish plugin\n\n- **Plugin ID**: {#0}\n- **Name**: {#1}\n\nCreated automatically by FlowBench Marketplace.'),
    ('未読み込み', '未加载', 'not loaded'),
    ('読み取りに失敗しました', '读取失败', 'Read failed'),
    ('初回の公開はブラウザーで認証するか、GitHub トークンを入力してください', '首次发布请在浏览器中确认授权，或填写 GitHub Token', 'Confirm authorization in the browser for the first publish, or enter a GitHub Token'),
    ('更新', '更新', 'Update'),
    ('インストール', '安装', 'Install'),
    ('{#0} / {#1} 個のプラグインが一致{#2}{#3}', '匹配 {#0} / {#1} 个插件{#2}{#3}', '{#0} / {#1} plugin(s) match{#2}{#3}'),
    ('プラグイン {#0} 個{#1}{#2}', '共 {#0} 个插件{#1}{#2}', '{#0} plugin(s){#1}{#2}'),
    ('無効', '已禁用', 'disabled'),
    ('読み込み失敗', '加载失败', 'load failed'),
    ('受信 KB/s', '下行 KB/s', 'Download KB/s'),
    ('送信 KB/s', '上行 KB/s', 'Upload KB/s'),
    ('グラフを一時停止', '暂停绘图', 'Pause Charts'),
    ('履歴をクリア', '清空历史', 'Clear History'),
    ('CSV をエクスポート', '导出 CSV', 'Export CSV'),
    ('メモリ', '内存', 'Memory'),
    ('TCP 接続数', 'TCP 连接数', 'TCP Connections'),
    ('プロセス数', '进程数', 'Processes'),
    ('監視データをエクスポート', '导出监控数据', 'Export monitoring data'),
    ('CSV ファイル (*.csv)', 'CSV 文件 (*.csv)', 'CSV files (*.csv)'),
    ('表示期間', '时间窗', 'Window'),
    ('1 分', '1 分钟', '1 min'),
    ('5 分', '5 分钟', '5 min'),
    ('15 分', '15 分钟', '15 min'),
    ('CPU・メモリの推移', 'CPU / 内存趋势', 'CPU / Memory Trend'),
    ('ネットワーク通信量', '网络速率', 'Network Throughput'),
    ('データなし', '无权限', 'N/A'),
    ('グラフを再開', '继续绘图', 'Resume Charts'),
    ('データなし', '暂无数据', 'No data'),
    ('エクスポートできる監視履歴がありません', '当前没有可导出的监控历史', 'There is no monitoring history to export'),
    ('エクスポートしました', '导出成功', 'Exported'),
    ('監視データ {#0} 件をエクスポートしました', '已导出 {#0} 条监控记录', 'Exported {#0} monitoring samples'),
    ('エクスポートに失敗しました', '导出失败', 'Export failed'),
    ('設定 {#0} 項目を復元しました。変更前の設定は {#1} に保存しました。', '已恢复 {#0} 项偏好；原设置已保存为 {#1}。', 'Restored {#0} preferences; previous settings were saved as {#1}.'),
    ('カスタム…', '自定义…', 'Custom…'),
    ('テーマカラーを選択', '选择主题颜色', 'Choose Theme Color'),
    ('OK', '确定', 'OK'),
    ('色を編集', '编辑颜色', 'Edit Color'),
    ('赤', '红', 'Red'),
    ('緑', '绿', 'Green'),
    ('青', '蓝', 'Blue'),
    ('バックアップには外観・言語・テストの初期設定・トレイ・更新設定のみ含まれます。認可済みの対象、過去のテスト、トークン、プラグイン固有データ、検索履歴は含まれません。', '备份仅包含外观、语言、默认测试参数、托盘和更新偏好；不会包含授权目标、上次测试、访问令牌、插件私有数据或搜索历史。', 'Backups contain only appearance, language, test defaults, tray and update preferences; authorized targets, previous tests, access tokens, private plugin data and search history are excluded.'),
    ('設定をエクスポート…', '导出设置备份…', 'Export Settings…'),
    ('設定を復元…', '恢复设置备份…', 'Restore Settings…'),
    ('設定を初期化', '恢复偏好默认', 'Reset Preferences'),
    ('診断情報をコピー', '复制诊断摘要', 'Copy Diagnostics'),
    ('ログをエクスポート', '导出日志', 'Export Log'),
    ('ログフォルダを開く', '打开日志目录', 'Open Log Folder'),
    ('免責事項を表示', '查看免责声明', 'View Disclaimer'),
    ('更新を確認', '检查更新', 'Check for Updates'),
    ('GitHub：{#0}', 'GitHub 主页：{#0}', 'GitHub: {#0}'),
    ('確認中…', '检查中…', 'Checking…'),
    ('保存に失敗しました', '保存失败', 'Save Failed'),
    ('設定 {#0} を保存できません：{#1}', '无法保存设置：{#0}：{#1}', 'Could not save setting {#0}: {#1}'),
    ('保存しました', '已保存', 'Saved'),
    ('言語設定は再起動後に完全に反映されます', '界面语言将在重启后完全生效', 'Language fully applies after restart'),
    ('再起動が必要です', '需要重启', 'Restart Required'),
    ('言語設定を復元しました。再起動後に完全に反映されます。', '已恢复语言偏好，界面语言将在重启后完全生效。', 'The language preference was restored and will fully apply after restart.'),
    ('設定のバックアップをエクスポート', '导出设置备份', 'Export settings backup'),
    ('JSON 設定バックアップ (*.json)', 'JSON 设置备份 (*.json)', 'JSON settings backup (*.json)'),
    ('バックアップを保存しました', '备份已导出', 'Backup Exported'),
    ('安全な設定 {#0} 項目を保存しました。機密データは含まれていません。', '已写入 {#0} 项安全偏好，敏感数据未包含。', 'Saved {#0} safe preferences; sensitive data was excluded.'),
    ('設定のバックアップを復元', '恢复设置备份', 'Restore settings backup'),
    ('JSON 設定バックアップ (*.json);;すべてのファイル (*.*)', 'JSON 设置备份 (*.json);;所有文件 (*.*)', 'JSON settings backup (*.json);;All files (*.*)'),
    ('設定のバックアップを復元', '恢复设置备份', 'Restore Settings Backup'),
    ('バックアップ内の安全な設定で現在の設定を置き換えます。事前に settings.json.bak を作成します。認可済みの対象、トークン、プラグイン固有データ、テスト履歴は変更しません。続行しますか？', '将使用备份中的安全偏好覆盖当前偏好。恢复前会自动创建 settings.json.bak；授权目标、访问令牌、插件私有数据和测试历史不会改变。是否继续？', 'Safe preferences in the backup will replace the current preferences. A settings.json.bak file will be created first; authorized targets, access tokens, private plugin data and test history will not change. Continue?'),
    (' 許可リスト外の {#0} 項目は安全に無視しました。', ' 已安全忽略 {#0} 个非白名单字段。', ' Safely ignored {#0} non-allowlisted fields.'),
    ('設定を復元しました', '设置已恢复', 'Settings Restored'),
    ('外観・言語・テストの初期設定・トレイ・更新設定を初期化します。同意情報、認可済みの対象、トークン、プラグイン、固有データは削除しません。続行しますか？', '将重置外观、语言、默认测试参数、托盘和更新偏好。授权状态、授权目标、访问令牌、插件及其私有数据不会被清除。是否继续？', 'This resets appearance, language, test defaults, tray and update preferences. Consent, authorized targets, access tokens, plugins and private plugin data will not be cleared. Continue?'),
    ('設定を初期化しました', '偏好已重置', 'Preferences Reset'),
    ('既定の設定 {#0} 項目を復元しました。機密データは保持しています。', '已恢复 {#0} 项默认偏好，敏感状态保持不变。', 'Restored {#0} default preferences; sensitive state was preserved.'),
    ('ダーク', '深色', 'Dark'),
    ('ライト', '浅色', 'Light'),
    ('パッケージ版', '打包版本', 'Packaged'),
    ('ソース版', '源码运行', 'Source'),
    ('プライバシー：トークン、認可済み・テスト対象、プラグイン固有データ、検索履歴、ローカルの絶対パスは含まれません。', '隐私：已排除令牌、授权目标、测试目标、插件私有数据、搜索历史和本机绝对路径。', 'Privacy: tokens, authorized/test targets, private plugin data, search history and absolute local paths are excluded.'),
    ('機密情報を除いた診断情報をコピーしました。', '脱敏诊断摘要已复制到剪贴板。', 'The redacted diagnostic summary was copied to the clipboard.'),
    ('ログをエクスポート', '导出日志', 'Export log'),
    ('ログファイル (*.log *.txt)', '日志文件 (*.log *.txt)', 'Log files (*.log *.txt)'),
    ('{#0} 件', '{#0} 条记录', '{#0} entries'),
    ('テーマカラーを保存できません：{#0}', '无法保存主题颜色：{#0}', 'Could not save the theme color: {#0}'),
    ('外観', '外观', 'Appearance'),
    ('ダークモード', '深色模式', 'Dark mode'),
    ('Fluent ダークテーマ', 'Fluent 深色主题', 'Fluent dark theme'),
    ('テーマカラー', '主题颜色', 'Theme color'),
    ('ボタンやハイライトの色を変更します。すぐに反映されます', '按钮、进度环等强调色，选择后立即生效', 'Accent color for buttons and highlights; applies instantly'),
    ('画面アニメーション', '页面动画', 'Page animations'),
    ('画面切り替えとコントロールの段階的な表示を有効にします', '启用页面切换和控件依次浮现效果', 'Enable page transitions and staggered control reveals'),
    ('閉じるときにトレイへ最小化', '关闭时最小化到托盘', 'Minimize to tray on close'),
    ('ウィンドウを閉じても、トレイでアプリを実行し続けます', '关闭窗口时程序将驻留系统托盘', 'Keep app running in system tray when closing window'),
    ('起動時に更新を自動確認', '启动时自动检查更新', 'Auto-check for updates on launch'),
    ('新しいバージョンが利用できるときに通知します', '发现新版本时弹窗提示', 'Show notification when new version is available'),
    ('自動（システム）', '跟随系统', 'Auto (system)'),
    ('簡体字中国語', '简体中文', 'Simplified Chinese'),
    ('言語', '界面语言', 'Language'),
    ('システムの言語に合わせる（既定）', '跟随系统语言自动选择（默认）', 'Auto-follow system language (default)'),
    ('初期設定', '默认参数', 'Defaults'),
    ('既定の同時実行数', '默认并发线程', 'Default concurrency'),
    ('初期スレッド数', '新会话的初始线程数', 'Initial thread count'),
    ('タイムアウト（ms）', '超时(ms)', 'Timeout (ms)'),
    ('リクエストごとの制限時間', '单请求超时时间', 'Per-request timeout'),
    ('既定のレート上限', '默认速率上限(QPS)', 'Default rate cap'),
    ('トークンバケットの補充レート', '令牌桶填充速率', 'Token bucket fill rate'),
    ('既定のテスト時間（秒）', '默认持续时间(秒)', 'Default duration (s)'),
    ('新しいテストの初期実行時間', '新会话的初始测试时长', 'Initial test duration for new sessions'),
    ('既定のパケットサイズ（バイト）', '默认报文大小(字节)', 'Default packet size (bytes)'),
    ('TCP・UDP・プラグインプロトコルのペイロードサイズ', 'TCP、UDP 与插件协议的发送载荷大小', 'Payload size for TCP, UDP and plugin protocols'),
    ('バックアップと診断', '备份与诊断', 'Backup & Diagnostics'),
    ('監査ログ', '审计日志', 'Audit Log'),
    ('このアプリについて', '关于', 'About'),
    ('適法に許可されたテストにのみ使用してください。', '仅用于合法授权的性能测试。', 'For legally authorized testing only.'),
    ('作者', '作者', 'Author'),
    ('テーマ設定を保存できません：{#0}', '无法保存主题设置：{#0}', 'Could not save the theme setting: {#0}'),
    ('アニメーション設定を保存できません：{#0}', '无法保存动画设置：{#0}', 'Could not save the animation setting: {#0}'),
    ('言語設定を保存できません：{#0}', '无法保存语言设置：{#0}', 'Could not save the language setting: {#0}'),
    ('不明', '未知', 'Unknown'),
    ('はい', '是', 'Yes'),
    ('いいえ', '否', 'No'),
    ('機密情報を除いた診断概要', '脱敏诊断摘要', 'Redacted Diagnostic Summary'),
    ('生成日時：', '生成时间：', 'Generated: '),
    ('アプリのバージョン：', '应用版本：', 'App version: '),
    ('OS：', '操作系统：', 'OS: '),
    ('Python：', 'Python：', 'Python: '),
    ('PySide6 / Qt：', 'PySide6 / Qt：', 'PySide6 / Qt: '),
    ('Fluent Widgets：', 'Fluent Widgets：', 'Fluent Widgets: '),
    ('テーマ／アクセントカラー：', '主题 / 强调色：', 'Theme / accent: '),
    ('言語：', '语言：', 'Language: '),
    ('画面アニメーション：', '页面动画：', 'Page animations: '),
    ('既定のスレッド数／タイムアウト／QPS：', '默认线程 / 超时 / QPS：', 'Defaults threads / timeout / QPS: '),
    ('既定の時間／パケット：', '默认时长 / 数据包：', 'Default duration / packet: '),
    ('トレイ最小化／自動更新：', '最小化到托盘 / 自动更新：', 'Tray minimize / auto update: '),
    ('設定フォルダの書き込み可否：', '设置目录可写：', 'Settings directory writable: '),
    ('監査ログ：', '审计日志：', 'Audit log: '),
    ('クラッシュログ：', '崩溃日志：', 'Crash log: '),
    ('プラグイン（総数／実行中／無効／エラー／未読み込み）：', '插件（总数/运行/禁用/错误/未加载）：', 'Plugins (total/running/disabled/error/unloaded): '),
    ('エクスポートに失敗しました', '导出失败', 'Export Failed'),
    ('設定のバックアップを書き込めません：{#0}', '无法写入设置备份：{#0}', 'Could not write the settings backup: {#0}'),
    ('復元に失敗しました', '恢复失败', 'Restore Failed'),
    ('バックアップが無効、非対応、または書き込めません：{#0}', '备份无效、版本不兼容或无法写入：{#0}', 'The backup is invalid, incompatible, or could not be written: {#0}'),
    ('初期化に失敗しました', '重置失败', 'Reset Failed'),
    ('既定の設定を保存できません：{#0}', '无法保存默认偏好：{#0}', 'Could not save default preferences: {#0}'),
    ('システム', '跟随系统', 'System'),
    ('利用可能（{#0} バイト）', '可用（{#0} 字节）', 'Available ({#0} bytes)'),
    ('見つかりません', '不存在', 'Not found'),
    ('コピーに失敗しました', '复制失败', 'Copy Failed'),
    ('診断情報を作成できません：{#0}', '无法生成诊断摘要：{#0}', 'Could not create the diagnostic summary: {#0}'),
    ('ネットワーク負荷テストと性能監視', '网络压力测试与性能监控', 'Network Stress Testing & Performance Monitoring'),
    ('タイムアウト', '超时', 'Timeout'),
    ('接続拒否', '连接被拒', 'Connection refused'),
    ('接続リセット', '连接被重置', 'Connection reset'),
    ('ネットワークに到達できません', '网络不可达', 'Unreachable'),
    ('DNS 名前解決失敗', 'DNS 解析失败', 'DNS resolution failed'),
    ('TLS ハンドシェイク失敗', 'TLS 握手失败', 'TLS handshake failed'),
    ('証明書エラー', '证书错误', 'Certificate error'),
    ('接続が閉じられました', '连接已关闭', 'Connection closed'),
    ('接続エラー', '连接错误', 'Connection error'),
    ('ICMP 応答なし', 'ICMP 无响应', 'ICMP no reply'),
    ('OS エラー {#0}', '系统错误 {#0}', 'OS error {#0}'),
    ('概要をコピー', '复制摘要', 'Copy Summary'),
    ('レポートをエクスポート', '导出报告', 'Export Report'),
    ('テストはまだ実行していません。', '尚未执行测试。', 'No test executed yet.'),
    ('設定をエクスポート', '导出配置', 'Export Config'),
    ('JSON 設定 (*.json)', 'JSON 配置 (*.json)', 'JSON Config (*.json)'),
    ('設定をインポート', '导入配置', 'Import Config'),
    ('JSON 設定 (*.json);;すべてのファイル (*.*)', 'JSON 配置 (*.json);;所有文件 (*.*)', 'JSON Config (*.json);;All Files (*.*)'),
    ('https://example.com\n127.0.0.1\napi.example.com', 'https://example.com\n127.0.0.1\napi.example.com', 'https://example.com\n127.0.0.1\napi.example.com'),
    ('プラグインの対象', '插件目标', 'Plugin Targets'),
    ('準備完了', '就绪', 'Ready'),
    ('最新のエラー：—', '最近失败原因：—', 'Last error: —'),
    ('開始', '开始测试', 'Start'),
    ('停止', '停止测试', 'Stop'),
    ('注意：すべての対象で許可の確認が必要です。レートはトークンバケットで制限されます。', '提示：所有目标须先通过授权确认；速率与并发受令牌桶限速保护。', 'Note: every target requires authorization; rate is capped by token bucket.'),
    ('設定のプレビューです。実測値は上に表示されます。', '配置预览 · 不会自动开始测试，实际性能以运行统计为准。', 'Configuration preview only; measured performance appears above.'),
    ('合計 {#0} 秒', '共 {#0} 秒', '{#0} seconds in total'),
    ('対象ごとに {#0} スレッド × {#1} 対象', '每目标 {#0} 个线程 × {#1} 个目标', '{#0} threads per target × {#1} targets'),
    ('対象ごとに上限 {#0} QPS × {#1} 対象。実測レートではありません。', '每目标上限 {#0} QPS × {#1} 个目标；并非实测速率。', '{#0} QPS cap per target × {#1} targets; not a measured rate.'),
    ('無効な対象アドレスが {#0} 件あります。先に修正してください。', '有 {#0} 个地址无法解析，请先修正。', '{#0} invalid target address(es); correct them first.'),
    ('認可をすべて削除', '清空授权记录', 'Clear Authorizations'),
    ('保存済みの対象の認可をすべて削除します。次回のテスト前に再確認が必要になります。続行しますか？', '将删除本机保存的全部目标授权。下次测试这些目标时需要重新确认，是否继续？', 'All saved target authorizations will be removed. You will need to confirm them again before the next test. Continue?'),
    ('対象の認可をすべて削除しました', '已清空全部目标授权记录', 'All target authorizations cleared'),
    ('クリアしました', '已清空', 'Cleared'),
    ('対象の認可を削除しました', '目标授权记录已删除', 'Target authorizations removed'),
    ('負荷テストを開始（{#0} 対象）：{#1} スレッド={#2} レート={#3} 時間={#4} 秒', '开始压测({#0}目标): {#1} threads={#2} rate={#3} duration={#4}s', 'Starting stress test ({#0} target(s)): {#1} threads={#2} rate={#3} duration={#4}s'),
    ('テスト実行中...', '测试进行中...', 'Test in progress...'),
    ('起動中...', '启动中...', 'Starting...'),
    ('負荷テストを手動で停止しました', '手动停止压测', 'Stress test stopped manually'),
    ('完了', '已完成', 'Completed'),
    ('対象 {#0} 件  |  実行時間 {#1}\n', '共 {#0} 个目标  |  持续 {#1}\n', '{#0} targets  |  Duration {#1}\n'),
    ('合計 {#0}  成功 {#1}  失敗 {#2}（エラー率 {#3}%）\n', '合计 {#0}  成功 {#1}  失败 {#2}（错误率 {#3}%）\n', 'Total {#0}  success {#1}  failed {#2} (error rate {#3}%)\n'),
    ('平均遅延 {#0} ms   P50 {#1} ms   P90 {#2} ms   P99 {#3} ms\n', '平均延迟 {#0} ms   P50 {#1} ms   P90 {#2} ms   P99 {#3} ms\n', 'Avg latency {#0} ms   P50 {#1} ms   P90 {#2} ms   P99 {#3} ms\n'),
    ('総送信量 {#0}   レート上限 {#1} QPS', '总发送流量 {#0}   速率上限 {#1} QPS', 'Total sent {#0}   Rate cap {#1} QPS'),
    (', ', '，', ', '),
    ('x', '×', 'x'),
    ('負荷テスト完了：合計={#0} 成功={#1} 失敗={#2} エラー={#3}', '压测完成: total={#0} success={#1} fail={#2} errors={#3}', 'Stress test finished: total={#0} success={#1} fail={#2} errors={#3}'),
    ('対象をインポートしました', '已导入目标', 'Targets imported'),
    ('プラグインから {#0} 件を取得しました', '来自插件：共 {#0} 个', 'From plugin: {#0} item(s)'),
    ('レポートの概要をコピーしました', '报告摘要已复制到剪贴板', 'Report summary copied to clipboard'),
    ('形式を選択', '选择导出格式', 'Choose format'),
    ('レポートをエクスポート', '导出报告', 'Export report'),
    ('すべてのファイル (*.*)', '所有文件 (*.*)', 'All files (*.*)'),
    ('集計レポート', '汇总报告', 'Summary Report'),
    ('エクスポートできません', '无法导出', 'Cannot Export'),
    ('エクスポートする前に少なくとも 1 件の対象を入力してください', '请至少填写一个目标地址', 'Enter at least one target before exporting'),
    ('{#0} に保存しました', '已保存到 {#0}', 'Saved to {#0}'),
    ('設定をエクスポートしました：{#0}', '配置已导出: {#0}', 'Config exported: {#0}'),
    ('インポートできません', '无法导入', 'Cannot Import'),
    ('テストを実行中です。先に停止してください', '测试正在运行中，请先停止', 'Test is running; stop it first'),
    ('インポートに失敗しました', '导入失败', 'Import Failed'),
    ('設定ファイルの形式が無効です', '配置文件格式无效', 'Config file has invalid format'),
    ('バージョンについて', '版本提示', 'Version Notice'),
    ('新しい FlowBench（v{#0}）の設定ファイルです。一部の設定は無視される場合があります。', '配置文件来自更新版本的 FlowBench（v{#0}），部分设置可能无法识别。', 'Config is from a newer FlowBench (v{#0}); some settings may be ignored.'),
    ('対象 {#0} 件を読み込みました', '已加载 {#0} 个目标配置', 'Loaded {#0} target(s)'),
    ('設定をインポートしました：{#0}', '配置已导入: {#0}', 'Config imported: {#0}'),
    ('対象の設定', '目标配置', 'Target Configuration'),
    ('対象アドレス（1 行につき 1 件、複数対象の同時テストに対応）', '目标地址（每行一个，支持多目标同时测试）', 'Targets (one per line; multiple targets run in parallel)'),
    ('同時実行スレッド数（対象ごと）', '并发线程数（每目标）', 'Concurrency Threads (per target)'),
    ('実行時間', '持续时间', 'Duration'),
    ('秒', '秒', 'sec'),
    ('分', '分钟', 'min'),
    ('時間', '小时', 'hour'),
    ('日', '天', 'day'),
    ('ヘッダー（HTTP、任意）', '请求头(HTTP, 可选)', 'Headers (HTTP, optional)'),
    ('認可済みの対象', '已授权目标', 'Authorized Targets'),
    ('状態', '运行状态', 'Status'),
    ('成功', '成功', 'Success'),
    ('平均遅延（ms）', '平均延迟(ms)', 'Avg Latency (ms)'),
    ('稼働スレッド数', '活跃线程', 'Active Threads'),
    ('テスト計画', '测试计划', 'Test plan'),
    ('対象数', '目标数量', 'Target count'),
    ('合計同時実行数', '总并发上限', 'Total concurrency'),
    ('合計レート上限', '总速率上限', 'Total rate limit'),
    ('対象を追加すると、許可の確認状況を表示します。', '添加目标后显示授权进度。', 'Add targets to see authorization progress.'),
    ('（なし）', '（暂无）', '(none)'),
    ('削除に失敗しました', '清空失败', 'Clear Failed'),
    ('認可の変更を保存できません：{#0}', '授权记录无法保存：{#0}', 'Could not save authorization changes: {#0}'),
    ('少なくとも 1 件の対象を入力してください', '请至少输入一个目标地址', 'Enter at least one target'),
    ('対象ごとのレート上限は {#0} QPS、合計 {#1} 対象（合計約 {#2} QPS）です。すべての対象への許可と、このレートへの対応能力を再確認してください。', '每个目标速率上限 {#0} QPS，共 {#1} 个目标（合计约 {#2} QPS）。请再次确认您拥有全部目标授权，且目标可承受该速率。', 'Rate cap is {#0} QPS per target, {#1} target(s) total (~{#2} QPS combined). Confirm again that all targets are authorized and can handle this rate.'),
    ('負荷テストを開始できません。設定を確認してください', '无法启动压测，请检查配置', 'Cannot start stress test, check configuration'),
    ('起動前に負荷テストを中止しました', '压测在启动前已取消', 'Stress test cancelled before startup'),
    ('実行中 · 対象 {#0} 件', '运行中 · {#0} 个目标', 'Running · {#0} targets'),
    ('最新のエラー：{#0}', '最近失败原因：{#0}', 'Last error: {#0}'),
    ('秒', '秒', 's'),
    ('• {#0}://{#1}  合計 {#2} 成功 {#3} 失敗 {#4}（{#5}%）平均 {#6} ms  {#7}\n', '• {#0}://{#1}  共 {#2} 成功 {#3} 失败 {#4}（{#5}%）平均 {#6} ms  {#7}\n', '• {#0}://{#1}  total {#2} ok {#3} fail {#4} ({#5}%) avg {#6} ms  {#7}\n'),
    ('対象 {#0}://{#1}  |  実行時間 {#2}\n', '目标 {#0}://{#1}  |  持续 {#2}\n', 'Target {#0}://{#1}  |  Duration {#2}\n'),
    ('合計 {#0}  成功 {#1}  失敗 {#2}（エラー率 {#3}%）\n', '总请求 {#0}  成功 {#1}  失败 {#2}（错误率 {#3}%）\n', 'Total {#0}  Success {#1}  Failed {#2} (error rate {#3}%)\n'),
    ('失敗の原因：{#0}', '失败原因分布：{#0}', 'Failure reasons: {#0}'),
    ('対象なし', '无目标', 'No targets'),
    ('プラグインから対象が返されませんでした', '该插件未返回任何目标', 'The plugin returned no targets'),
    ('レポートなし', '暂无报告', 'No report'),
    ('先に負荷テストを実行してください', '请先执行一次压测', 'Run a stress test first'),
    ('現在のレポートにコピーできる内容がありません', '当前报告没有可复制的内容', 'The current report has no content to copy'),
    ('レポートをエクスポートしました：{#0}', '报告已导出: {#0}', 'Report exported: {#0}'),
    ('レポートをエクスポートしました（CSV）：{#0}', '报告已导出(CSV): {#0}', 'Report exported (CSV): {#0}'),
    ('{#0} → {#1}', '{#0} → {#1}', '{#0} → {#1}'),
    ('レポートをエクスポートしました（{#0}）：{#1}', '报告已导出({#0}): {#1}', 'Report exported ({#0}): {#1}'),
    ('設定が無効です', '配置无效', 'Invalid Config'),
    ('設定ファイルに対象がありません', '配置文件缺少目标地址', 'Config file is missing targets'),
    ('設定ファイルの形式が不正です：{#0}', '配置文件格式错误：{#0}', 'Bad config format: {#0}'),
    ('ファイルに書き込めません：{#0}', '无法写入文件：{#0}', 'Cannot write file: {#0}'),
    ('ファイルを読み込めません：{#0}', '无法读取文件：{#0}', 'Cannot read file: {#0}'),
    ('設定ファイルのバージョン指定が無効です', '配置文件版本字段无效', 'Config file has an invalid version field'),
    ('ポート', '端口', 'Port'),
    ('プロトコル', '协议', 'Protocol'),
    ('レート上限（QPS）', '速率上限(QPS)', 'Rate Limit (QPS)'),
    ('認可済み {#0} / {#1} · 確認が必要です', '已授权 {#0} / {#1} · 尚有目标需要确认', 'Authorized {#0} / {#1} · Confirmation required'),
    ('認可済み {#0} / {#1} · すべての対象を認可済み', '已授权 {#0} / {#1} · 所有目标已授权', 'Authorized {#0} / {#1} · All targets authorized'),
    ('無効な対象：{#0}', '无效目标：{#0}', 'Invalid target: {#0}'),
    ('高レートが確認されていないため、中止しました', '高速率未确认，测试已取消', 'High rate not confirmed; cancelled'),
    ('プラグインから対象を取得できません', '插件目标获取失败', 'Plugin target fetch failed'),
]
_EXTRA = {
    "Japanese Language Pack": "日本語言語パック",
    "日语语言包": "日本語言語パック",
    "English": "英語",
    "On": "オン", "Off": "オフ",
    "开启": "オン", "关闭": "閉じる",
    "开": "オン", "关": "オフ",
    "Open": "開く", "Save": "保存", "Save As": "名前を付けて保存",
    "&Open": "開く(&O)", "&Save": "保存(&S)", "&Cancel": "キャンセル(&C)",
    "&Yes": "はい(&Y)", "&No": "いいえ(&N)",
    "Cut": "切り取り", "Copy": "コピー", "Paste": "貼り付け",
    "Undo": "元に戻す", "Redo": "やり直し", "Select All": "すべて選択",
    "Delete": "削除", "Cancel": "キャンセル", "Close": "閉じる",
    "Minimize": "最小化", "Maximize": "最大化", "Restore": "元に戻す",
    "Show navigation menu": "ナビゲーションを表示",
    "Navigation": "ナビゲーション", "Back": "戻る", "More": "その他",
    "Original": "元の色", "New": "新しい色",
    "Apply": "適用", "Help": "ヘルプ", "Browse": "参照",
}
_DISCLAIMER = (
    "このツールは、ご自身が所有している対象、または書面でテストの許可を得た対象の性能テスト専用です。\n\n"
    "1. 無許可で他者のシステムに負荷テストを行うことは、多くの法域で違法です。\n"
    "2. レートと同時実行数が対象システムの処理能力を超えないようにしてください。\n"
    "3. すべてのテスト操作は監査ログに記録されます。\n"
    "4. このツールの使用に伴う結果については、使用者がすべての責任を負います。\n\n"
    "続行すると、上記の規約を読み、理解し、同意したものとみなされます。"
)
_SLOT = re.compile(r"\{#(\d+)\}")
_EXACT = {}
_PAIRS = {}
_TEMPLATES = []
_UNITS = {"sec": "秒", "秒": "秒", "min": "分", "分钟": "分",
          "hour": "時間", "小时": "時間", "day": "日", "天": "日"}
_NUMERIC_UNIT = re.compile(r"^([0-9][0-9,.]*\s+)(sec|min|hour|day|秒|分钟|小时|天)$")


def _pattern(source):
    parts = []
    offset = 0
    seen = set()
    for match in _SLOT.finditer(source):
        parts.append(re.escape(source[offset:match.start()]))
        key = "value" + match.group(1)
        parts.append(f"(?P={key})" if key in seen else f"(?P<{key}>[^\n]*?)")
        seen.add(key)
        offset = match.end()
    parts.append(re.escape(source[offset:]))
    return re.compile("".join(parts))


def _render(template, values):
    return _SLOT.sub(lambda match: values["value" + match.group(1)], template)


for _japanese, _chinese, _english in _RESOURCES:
    if not _SLOT.search(_chinese) and not _SLOT.search(_english):
        _PAIRS[(_chinese, _english)] = _japanese
    for _source in (_chinese, _english):
        if _SLOT.search(_source):
            if _source != _japanese and re.search(r"\w", _SLOT.sub("", _source)):
                _TEMPLATES.append((_pattern(_source), _japanese, len(_SLOT.sub("", _source))))
        else:
            _EXACT.setdefault(_source, _japanese)
_EXACT.update(_EXTRA)
_TEMPLATES.sort(key=lambda entry: entry[2], reverse=True)
_patched = False
_original = {}
_changed = weakref.WeakKeyDictionary()
_watcher = None
_qt_translator = None


def translate(value):
    if not isinstance(value, str) or not value:
        return value
    if value in _EXACT:
        return _EXACT[value]
    for pattern, japanese, _length in _TEMPLATES:
        match = pattern.fullmatch(value)
        if match:
            return _render(japanese, match.groupdict())
    unit = _NUMERIC_UNIT.fullmatch(value)
    if unit:
        return unit.group(1) + _UNITS[unit.group(2)]
    if "\n" in value:
        return "".join(translate(line.rstrip("\r\n")) + line[len(line.rstrip("\r\n")):]
                       for line in value.splitlines(keepends=True))
    return value


def _swap_refs(old, new):
    for module_name, module in list(sys.modules.items()):
        if module is None or not (module_name == "main" or module_name.startswith(("app.", "flowbench_plugin_"))):
            continue
        for name, value in list(vars(module).items()):
            if value is old:
                setattr(module, name, new)


def _set_text(widget, getter_name, setter_name, *arguments, converter=translate):
    getter = getattr(widget, getter_name, None)
    setter = getattr(widget, setter_name, None)
    if not callable(getter) or not callable(setter):
        return
    try:
        source = getter(*arguments)
        translated = converter(source)
        if source != translated:
            key = (getter_name, setter_name, arguments)
            previous = _changed.setdefault(widget, {}).get(key)
            original = previous[0] if previous and previous[1] == source else source
            setter(*arguments, translated)
            _changed[widget][key] = (original, translated)
    except (AttributeError, RuntimeError, TypeError):
        pass


def _convert_widget(widget):
    from PySide6.QtCore import QSignalBlocker
    from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QTextEdit

    blocker = QSignalBlocker(widget)
    if isinstance(widget, (QLineEdit, QPlainTextEdit, QTextEdit)):
        _set_text(widget, "placeholderText", "setPlaceholderText")
    else:
        if not widget.property("searchTerm"):
            _set_text(widget, "text", "setText")
        for getter, setter in (("getOnText", "setOnText"), ("getOffText", "setOffText")):
            _set_text(widget, getter, setter, converter=lambda value: {
                "On": "オン", "Off": "オフ", "开启": "オン", "关闭": "オフ"
            }.get(value, translate(value)))
        _set_text(widget, "title", "setTitle")
        for getter, setter in (("itemText", "setItemText"), ("tabText", "setTabText")):
            if callable(getattr(widget, getter, None)) and callable(getattr(widget, setter, None)):
                count = getattr(widget, "count", None)
                if callable(count):
                    for index in range(count()):
                        _set_text(widget, getter, setter, index)
    for getter, setter in (("toolTip", "setToolTip"), ("statusTip", "setStatusTip"),
                           ("windowTitle", "setWindowTitle"), ("accessibleName", "setAccessibleName"),
                           ("accessibleDescription", "setAccessibleDescription")):
        _set_text(widget, getter, setter)
    for action in widget.actions():
        _set_text(action, "text", "setText")
        _set_text(action, "toolTip", "setToolTip")
    del blocker


def convert_ui():
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    if application is None:
        return
    for widget in application.allWidgets():
        try:
            _convert_widget(widget)
        except (AttributeError, RuntimeError, TypeError):
            continue


def install():
    global _patched, _watcher, _qt_translator
    if _patched:
        convert_ui()
        return
    import app.services.plugins as plugins_module
    import app.ui.disclaimer as disclaimer_module
    import app.ui.i18n as i18n
    from PySide6.QtCore import QEvent, QObject, QTimer, QTranslator
    from PySide6.QtWidgets import QApplication

    original_l = i18n.L
    original_text = plugins_module._i18n_text
    original_disclaimer = disclaimer_module._disclaimer_text

    def japanese_l(chinese, english):
        direct = _PAIRS.get((chinese, english))
        if direct is not None:
            return direct
        for source in (english, chinese):
            translated = translate(source)
            if translated != source:
                return translated
        return original_l(chinese, english)

    def japanese_text(value):
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return japanese_l(str(value[0]), str(value[1]))
        return translate(original_text(value))

    def japanese_disclaimer():
        return _DISCLAIMER

    class JapaneseTranslator(QTranslator):
        def isEmpty(self):
            return False

        def translate(self, context, source_text, disambiguation=None, number=-1):
            return _EXACT.get(source_text, "")

    class TranslationWatcher(QObject):
        def __init__(self, parent):
            super().__init__(parent)
            self.pending = False

        def eventFilter(self, watched, event):
            if event.type() in (QEvent.Show, QEvent.LanguageChange) and not self.pending:
                self.pending = True
                QTimer.singleShot(0, self.refresh)
            return False

        def refresh(self):
            self.pending = False
            if _patched:
                convert_ui()

    _original.update(L=original_l, text=original_text, disclaimer=original_disclaimer)
    i18n.L = japanese_l
    plugins_module._i18n_text = japanese_text
    disclaimer_module._disclaimer_text = japanese_disclaimer
    _swap_refs(original_l, japanese_l)
    _swap_refs(original_text, japanese_text)
    _swap_refs(original_disclaimer, japanese_disclaimer)
    _patched = True
    application = QApplication.instance()
    if application is not None:
        _qt_translator = JapaneseTranslator(application)
        application.installTranslator(_qt_translator)
        _watcher = TranslationWatcher(application)
        application.installEventFilter(_watcher)
    convert_ui()


def uninstall():
    global _patched, _watcher, _qt_translator
    if not _patched:
        return
    import app.services.plugins as plugins_module
    import app.ui.disclaimer as disclaimer_module
    import app.ui.i18n as i18n
    from PySide6.QtCore import QSignalBlocker
    from PySide6.QtWidgets import QApplication

    _patched = False
    application = QApplication.instance()
    if application is not None:
        if _watcher is not None:
            application.removeEventFilter(_watcher)
            _watcher.deleteLater()
        if _qt_translator is not None:
            application.removeTranslator(_qt_translator)
            _qt_translator.deleteLater()
    _watcher = _qt_translator = None
    for module, name, key in ((i18n, "L", "L"), (plugins_module, "_i18n_text", "text"),
                              (disclaimer_module, "_disclaimer_text", "disclaimer")):
        current = getattr(module, name)
        original = _original[key]
        setattr(module, name, original)
        _swap_refs(current, original)
    for widget, changes in list(_changed.items()):
        try:
            blocker = QSignalBlocker(widget)
            for (getter, setter, arguments), (original, translated) in changes.items():
                if getattr(widget, getter)(*arguments) == translated:
                    getattr(widget, setter)(*arguments, original)
            del blocker
        except (AttributeError, RuntimeError, TypeError):
            continue
    _changed.clear()
    _original.clear()


class Plugin(FlowBenchPlugin):
    name = ("日语语言包", "Japanese Language Pack")
    title = "日本語言語パック"
    version = "1.0.2"
    author = "FlowBench"
    description = (
        "将 FlowBench 1.2.7 的界面、对话框和动态提示转换为日语，兼容深浅色主题；启用即生效。",
        "Japanese UI, dialogs and dynamic messages for FlowBench 1.2.7; supports light and dark themes."
    )
    icon = "LANGUAGE"
    category = "ui"

    def on_load(self, ctx):
        from app.services.plugins import plugin_manager

        for record in plugin_manager.records():
            if record.pid in ("russian", "zh_tw") and record.plugin is not None:
                raise RuntimeError("请先停用其他语言包 / Disable other language packs first")
        self._ctx = ctx
        install()

    def on_unload(self):
        uninstall()

    def create_widget(self, parent):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        from qfluentwidgets import BodyLabel, InfoBar, PrimaryPushButton, SimpleCardWidget, SubtitleLabel

        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(12)
        card = SimpleCardWidget(widget)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setSpacing(10)
        card_layout.addWidget(SubtitleLabel("日本語言語パック", card))
        for text in (
            "日本語の表示を有効にしました。新しいダイアログや動的な通知も日本語で表示します。",
            "ライト・ダークの両テーマに対応しています。入力内容や接続先、テスト設定は変更しません。",
            "反映されていない表示は、下のボタンで更新できます。元の言語に戻すには、このプラグインを無効にして再起動してください。",
            "ほかの言語パックとは同時に有効にしないでください。"
        ):
            label = BodyLabel(text, card)
            label.setWordWrap(True)
            card_layout.addWidget(label)
        button = PrimaryPushButton("表示を更新", card)

        def refresh():
            convert_ui()
            InfoBar.success("更新しました", "日本語の表示を更新しました。", parent=widget.window(), duration=2500)

        button.clicked.connect(refresh)
        card_layout.addWidget(button)
        layout.addWidget(card)
        layout.addStretch(1)
        return widget

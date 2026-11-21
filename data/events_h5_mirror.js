/* ============================================================
 * events.js — 事件追踪数据
 * ------------------------------------------------------------
 * 当前全球瞩目的重点事件（人工甄选 + 定期维护），驱动「事件追踪」页。
 * 每条事件 = 一张卡片；点开卡片看预测、现状、时间线、相关报道与相关标的。
 *
 * 【数据性质 —— 必读】
 * 本文件是**人工维护的叙事快照 + 实时行情叠加**的混合结构：
 *   · updated  = 该卡片「叙事部分」的人工维护截止时间，卡片上会明确标出
 *   · src      = 内容出处（人工整理所参考的来源），不保证逐条可核
 *   · 时间线节点用 k 字段区分性质：
 *       k: 'fact'     —— 已由来源报道的既成事实（人工转述，非本终端核验）
 *       k: 'forecast' —— 预期 / 关键节点 / 条件性判断（预测，非事实）
 *   · metrics 里带 asset 字段的条目 = 该指标本身就是一个可实时报价的品种
 *     （asset = 行情键），渲染层会用 QuoteHub 的实时报价覆盖显示，
 *     并把 value 保留为「快照基准值」做对照；卡片上会标「实时」角标。
 *   · metrics 里不带 asset 的条目 = 预测概率、人工指数、人工估算，
 *     仍然是维护时点的人工数值，无法实时化，渲染层会明确标注。
 *   · assets  = 该事件的相关标的清单，卡片会实时显示这些标的的当前报价。
 * 渲染层会把「实时行情」与「人工快照」明确区分开，避免把预期当成已发生的事实。
 * ============================================================ */
(function (w) {
  'use strict';

  w.EVENTS = [
    {
      id: 'iran',
      icon: '🛢️',
      title: '美伊冲突 · 霍尔木兹海峡',
      cat: '地缘 · 能源',
      status: '僵持中',
      statusCls: 'warn',
      hot: 3,
      updated: '2026-11-05 18:10',
      summary: '伊朗要求美国完整回应其七项条件后才开放霍尔木兹海峡；据来源报道，特朗普承认美军对伊朗防空设施发动「小规模攻击」。海峡运输受阻推升能源与取暖油成本，但油价因和谈预期连续回落，市场在「和谈 vs 供应风险」之间反复拉锯。',
      /* snapTs = 这个人工快照的真实录入时刻；行情源没报价时，
       * 渲染层会显示「10 天前」并每天自动走，不再让人误以为是当前值。 */
      headline: { label: '布伦特原油', value: '100.8', sub: '11-04 早盘人工录入（快照基准）',
        asset: 'BRENT', snapTs: '2026-11-04 09:30' },
      metrics: [
        /* 【v289】封锁已持续天数按日期自动推算（自 11-07 停火中断起算），
         * 不再人工维护；daysSince 由渲染层 eventAutoMetric 每次渲染时计算。 */
        { name: '海峡封锁已持续', value: 6, unit: '天', note: '自 11-07 停火中断 · 按日期自动推算',
          color: '#ff9fb0', max: 30, auto: { type: 'daysSince', date: '2026-11-07' } },
        { name: '布伦特原油（快照）', value: 100.8, note: '美元/桶 · 维护时点值', color: '#f783ac', asset: 'BRENT' },
        { name: 'WTI 原油（快照）', value: 96.7, note: '美元/桶 · 维护时点值', color: '#f783ac', asset: 'WTI' },
        { name: '美国取暖油均价（快照）', value: 6.19, note: '美元/加仑 同比+76% · 维护时点值', color: '#ff9fb0' },
        /* 这一格原来是「人工指数 35」—— 纯手填，图表永远不动。
         * 现在接 Polymarket：11-30 前「某日至少有 10 艘船通行海峡」的合约价，
         * 当前 35.5%，与原来的人工数值几乎一致，但它会自己走。 */
        { name: '海峡恢复通行概率', value: 35.5, unit: '%',
          note: 'Polymarket：11-30 前某日 ≥10 艘船通行', color: '#ffd43b',
          pred: { q: 'hormuz ships transit', event: 'ships transit the Strait of Hormuz on any day by November',
                  market: 'at least 10 ships', outcome: 'Yes' } },
        /* 【v172】原来那条「停火延续至年底」的盘口已经**结算为 No**（停火 11-07 前后中断），
         * 继续挂着一个 0% 没有信息量。换成 Polymarket 上同一事件**仍在交易**的三条，
         * 分别对应「解除封锁 / 航道恢复 / 核协议」三条真实路径。 */
        { name: '美国宣布解除封锁（12-31 前）', value: 37, unit: '%',
          note: 'Polymarket：US announces end of Iranian blockade by 12-31', color: '#ffd43b',
          pred: { q: 'US announces end of Iranian blockade', event: 'US announces end of Iranian blockade by',
                  market: 'December 31, 2026', outcome: 'Yes' } },
        { name: '海峡通行恢复正常（12-31 前）', value: 8.5, unit: '%',
          note: 'Polymarket：Strait of Hormuz traffic returns to normal by 12-31', color: '#ffd43b',
          pred: { q: 'Strait of Hormuz traffic returns to normal', event: 'Strait of Hormuz traffic returns to normal by December 31',
                  market: 'December 31', outcome: 'Yes' } },
        { name: '美伊最终核协议（12-31 前）', value: 9.5, unit: '%',
          note: 'Polymarket：US-Iran Final Nuclear Deal by 12-31', color: '#ffd43b',
          pred: { q: 'US-Iran Final Nuclear Deal', event: 'US-Iran Final Nuclear Deal by',
                  market: 'December 31, 2026', outcome: 'Yes' } }
      ],
      battleground: ['霍尔木兹海峡', '伊朗核谈判', '也门胡塞', '沙特联军', '卡塔尔斡旋'],
      timeline: [
        { t: '10月中', k: 'fact', txt: '来源报道：伊朗提出七项条件，作为开放海峡前提' },
        { t: '11-02', k: 'fact', txt: '来源报道：特朗普承认美军对伊朗防空设施「小规模攻击」' },
        { t: '11-03', k: 'fact', txt: '来源报道：伊朗称距离达成结束战争的协议「还很遥远」' },
        { t: '11-04', k: 'fact', txt: '来源报道：油价连跌后尾盘收复大部分跌幅，市场观望和谈' }
      ],
      themes: ['原油供给', '取暖油成本', '航运风险', '黄金避险'],
      newsKeys: ['伊朗', '霍尔木兹', '美伊', '胡塞', '中东', '原油', '油轮'],
      /* 【v289】newsMust 兜一道：裸「中东」「原油」会把「中东地缘局势缓和（风口研报）」
       * 这类无关稿抓进来；必须再命中主体词才归集到本卡。 */
      newsMust: ['伊朗', '美伊', '霍尔木兹', '海峡', '胡塞', '油轮', '德黑兰', '革命卫队'],
      /* 【v289】四件套之一 autoProgress：按事件逻辑组织优先级
       * （决定性：封锁解除/协议 > 阶段：军事升级/制裁 > 中性：外交沟通/基线僵持）。
       * 规则不覆盖 headline.value —— 本卡 headline 接布伦特实时报价，
       * 覆盖 value 会把价格位换成文字，只推进 status/statusCls。 */
      autoWindowH: 96,
      autoProgress: [
        /* P1 决定性：封锁解除 / 协议落地 */
        { re: '解除封锁|解除.{0,8}封锁|封锁解除|开放海峡|恢复通行|达成(核 )?协议|核协议达成|签署协议|停火协议',
          neg: '不会|未能|尚未|拒绝|遥远|难有|尚无|概率|预期|预测|料|或|可能|拟|计划|将|呼吁|敦促|要求|传闻|草案',
          status: '封锁解除', statusCls: 'live' },
        /* P2 阶段：军事升级（空袭 / 击落 / 扣押 / 触雷等已发生动作；
         * 「准备」「威胁」「将」等前瞻表述被 neg 挡住） */
        { re: '空袭|袭击|打击|攻击|击落|击中|拦截|导弹|无人机|触雷|起火|扣押|捕获|封锁|征用',
          neg: '将|拟|计划|准备|威胁|扬言|警告|若|恐|担忧|担心|是否|传闻|消息人士|呼吁|敦促',
          status: '军事升级', statusCls: 'warn' },
        /* P3 阶段：制裁加码（「解除制裁」反向命中 P1 语义时不在此误报） */
        { re: '制裁|禁运|冻结资产|黑名单|出口管制',
          neg: '解除|取消|放松|豁免|将|拟|计划|考虑|呼吁|或',
          status: '制裁加码', statusCls: 'warn' },
        /* P4 中性：外交沟通（谈判 / 通话 / 斡旋 / 传话） */
        { re: '谈判|核谈|磋商|会谈|通电话|通话|斡旋|调解|转达|传话|信件|回信',
          neg: '尚未|未能|不会|拒绝|遥远|回应|表态|评论',
          status: '对话推进', statusCls: 'watch' },
        /* v291【删基线规则】基线由本卡静态 status 决定，规则只做阶段推进 */
      ],
      /* 【v289】四件套之二 seedNews：人工核实的兜底资讯（11-05 财联社快讯实录），
       * 实时源有同类新闻时按标题去重、实时优先；只标日期，不编造分钟。 */
      seedNews: [
        { id: 'iran-seed-1', dateLabel: '11-05', level: 'A', cat: '地缘',
          title: '伊朗称霍尔木兹海峡两艘油轮起火 两架无人机遭捕获',
          src: '财联社' },
        { id: 'iran-seed-2', dateLabel: '11-05', level: 'A', cat: '地缘',
          title: '伊朗称击落一架美军MQM-170无人机',
          src: '财联社' },
        { id: 'iran-seed-3', dateLabel: '11-05', level: 'A', cat: '地缘',
          title: '伊朗称一艘“违规”油轮在霍尔木兹海峡触雷起火',
          src: '财联社' },
        { id: 'iran-seed-4', dateLabel: '11-05', level: 'A', cat: '外交',
          title: '伊朗外长与沙特和卡塔尔外交大臣通电话 讨论地区局势',
          src: '财联社' },
        { id: 'iran-seed-5', dateLabel: '11-05', level: 'A', cat: '地缘',
          title: '沙特主导联军称拦截一枚胡塞武装发射的弹道导弹',
          src: '财联社' },
        { id: 'iran-seed-6', dateLabel: '11-05', level: 'B', cat: '外交',
          title: '伊朗高官向普京转达最高领袖讯息',
          src: '财联社' }
      ],
      assets: ['BRENT', 'WTI', 'GOLD'],
      src: '路透社 / 华尔街日报 / 央视新闻（11-03 ~ 11-04）'
    },
    {
      id: 'fed',
      icon: '🏛️',
      title: '美联储政策路径',
      cat: '央行 · 利率',
      status: '跟踪中',
      statusCls: 'watch',
      hot: 3,
      updated: '2026-11-05 18:10',
      summary: '据来源报道，9月JOLTS职位空缺意外升至752.3万、工厂订单强劲，美债收益率与美元同步走高；Polymarket 上「12月加息25bp」的合约价约 77.5%。加息预期升温压制风险资产与贵金属，是当前除选举外最主导的宏观主线。',
      /* v171：headline 也接 Polymarket —— 原来只有下面的指标行接了实时，
       * 上面这个 77.5% 是写死的，同一页两个值（77.5 / 76.5）用户一眼就看出来。 */
      headline: { label: '12月加息概率', value: '77.5%', sub: 'Polymarket 合约价',
        pred: { q: 'fed decision december', event: 'Fed Decision in December',
                market: 'increase interest rates by 25', outcome: 'Yes' } },
      metrics: [
        /* 【v289】跟踪推进天数按日期自动推算（自 11-03 JOLTS 数据日建档起算） */
        { name: '本卡跟踪已持续', value: 9, unit: '天', note: '自 11-03 JOLTS 数据日建档 · 按日期自动推算',
          color: '#8ce99a', max: 60, auto: { type: 'daysSince', date: '2026-11-03' } },
        { name: '12月加息概率', value: 77.5, unit: '%',
          note: 'Polymarket：12 月 FOMC 加息 25bp 合约价', color: '#8ce99a',
          pred: { q: 'fed decision december', event: 'Fed Decision in December',
                  market: 'increase interest rates by 25', outcome: 'Yes' } },
        /* v172：同一事件（Fed Decision in December）的另外两种结果 —— 加息 76.5% 只是分布的一端，
         * 把「维持不变」和「降息」一起放上来，才看得出市场对 12 月的完整定价。 */
        { name: '12月维持利率不变', value: 21.5, unit: '%',
          note: 'Polymarket：12 月 FOMC 按兵不动', color: '#8ce99a',
          pred: { q: 'fed decision december', event: 'Fed Decision in December',
                  market: 'no change', outcome: 'Yes' } },
        { name: '12月降息 25bp', value: 0.7, unit: '%',
          note: 'Polymarket：12 月 FOMC 降息 25bp', color: '#8ce99a',
          pred: { q: 'fed decision december', event: 'Fed Decision in December',
                  market: 'decrease interest rates by 25', outcome: 'Yes' } },
        { name: '9月JOLTS职位空缺', value: 752.3, note: '万人 · 来源报道，超预期716.4万', color: '#8ce99a' },
        { name: '美债10Y收益率（快照）', value: 4.3, note: '%（走高）· 维护时点值', color: '#63e6be', asset: 'US10Y' },
        { name: '美元指数（快照）', value: 106, note: '高位运行 · 维护时点值', color: '#8ce99a', asset: 'DXY' }
      ],
      battleground: ['12月FOMC', '鲍威尔讲话', '点阵图', 'JOLTS', '非农就业'],
      timeline: [
        { t: '11-03', k: 'fact', txt: '来源报道：9月JOLTS职位空缺752.3万，超预期；裁员降至2025年3月来最低' },
        { t: '11-04', k: 'fact', txt: '来源报道：美债收益率与美元同步走高，贵金属与工业金属承压' },
        { t: '12月', k: 'forecast', txt: '关键节点：FOMC议息会议——市场押注79%概率加息（期货隐含）' }
      ],
      themes: ['加息预期', '美债收益率', '美元指数', '风险偏好'],
      /* 裸「加息 / 降息」已拿掉：这两词全球央行通用，留着会把欧央行、日银
       * 的稿子全抓进来（实跑：欧洲央行官员称12月加息 + 欧元区通胀创新高）。
       * 现在只留美联储专属词，再用 newsMust 兜一道、newsExcl 挡别的央行。 */
      newsKeys: ['美联储', '鲍威尔', 'FOMC', 'JOLTS', '非农', '美债', '美元指数'],
      newsMust: ['美联储', '鲍威尔', 'FOMC', '联储', '美元指数', '美债', '非农', 'JOLTS'],
      newsExcl: ['欧洲央行', '欧元区', 'ECB', '日本央行', '日银', '英国央行', '英央行'],
      /* 【v289】四件套之一 autoProgress：按议息日程推进
       * （决定性：利率决议公布 > 纪要/点阵图发布 > 议息临近 > 官员表态 > 数据/基线）。
       * 与 iran 同理不覆盖 headline.value —— headline 接 Polymarket 合约价，
       * 覆盖会把概率位换成文字；只推进 status/statusCls。 */
      autoWindowH: 96,
      autoProgress: [
        /* P1 决定性：利率决议公布（「预期加息/概率」类前瞻稿被 neg 挡住）。
         * v513【主体限定】实测误判:南非储备银行「维持利率不变」把本卡推成「决议公布」
         * ——「维持利率不变/利率决定」是全球央行通用词,必须用lookahead要求
         * 美联储/FOMC/联邦基金利率主体同现,他国央行动作一律不命中。 */
        { re: '(?=.*(?:美联储|FOMC|联邦基金利率))(美联储宣布|FOMC宣布|宣布加息|宣布降息|上调联邦基金利率|下调联邦基金利率|维持利率不变|利率决议公布|决议公布|利率决定|利率不变)',
          neg: '预期|概率|预计|料|或将|若|传闻|押注',
          status: '决议公布', statusCls: 'live' },
        /* P2 阶段：会议纪要 / 点阵图发布 */
        { re: '会议纪要|点阵图|纪要显示|经济预测摘要|纪要公布',
          neg: '将公布|即将|预期',
          status: '纪要发布', statusCls: 'live' },
        /* P3 阶段：议息临近（v513 同加主体限定:他国央行「利率决议」不命中） */
        { re: '(?=.*(?:美联储|FOMC))(议息会议|下次会议|FOMC会议|美联储会议|利率决议|决议前|会前|静默期)',
          neg: '尚有时日|取消|推迟',
          status: '议息临近', statusCls: 'watch' },
        /* P4 中性：官员表态（v513:「理事|主席称|官员称」为通用词,加美联储主体限定） */
        { re: '(?=.*(?:美联储|FOMC|联储))(鲍威尔|沃什|哈马克|威廉姆斯|古尔斯比|穆萨勒姆|票委|理事|主席称|主席表示|官员称|官员表示)',
          neg: '前任|此前曾任',
          status: '官员表态', statusCls: 'watch' },
        /* v291【删基线规则】基线由本卡静态 status 决定，规则只做阶段推进 */
      ],
      /* 【v289】四件套之二 seedNews：人工核实的兜底资讯（11-03 ~ 11-05 实录） */
      seedNews: [
        { id: 'fed-seed-1', dateLabel: '11-05', level: 'A', cat: '央行',
          title: '美联储12月加息的概率降至68.7%',
          src: '财联社' },
        { id: 'fed-seed-2', dateLabel: '11-05', level: 'A', cat: '央行',
          title: '美联储哈马克：渐进式加息是推动通胀回归2%的最佳路径',
          src: '财联社' },
        { id: 'fed-seed-3', dateLabel: '11-05', level: 'B', cat: '央行',
          title: '特朗普中期选举受挫 美联储主席沃什或卸下政治束缚',
          src: '财联社' },
        { id: 'fed-seed-4', dateLabel: '11-03', level: 'A', cat: '数据',
          title: '美国9月JOLTS职位空缺升至752.3万 超预期',
          src: '华尔街见闻' },
        { id: 'fed-seed-5', dateLabel: '11-04', level: 'B', cat: '市场',
          title: '美债收益率与美元同步走高 贵金属与工业金属承压',
          src: '华尔街见闻' }
      ],
      assets: ['US10Y', 'DXY', 'SPX', 'GOLD'],
      src: '华尔街见闻 / 汇通财经（11-03 ~ 11-04）'
    },
    {
      id: 'ukraine',
      icon: '🕊️',
      title: '俄乌会谈',
      phase: '会谈进行中',   /* 大字只显示稳定阶段短语，详细态势放简介小字 */
      cat: '地缘 · 外交',
      status: '会谈进行中',
      statusCls: 'live',
      hot: 3,
      updated: '2026-11-11 11:45',
      summary: '俄美乌三方会谈已于11月10日在阿联酋阿布扎比启动，为今年2月谈判停滞以来时隔9个月首次重启面对面磋商；首日闭门会谈约4小时，11日继续举行。重点议题为战后安全安排、局势降级、人道主义行动与和平后续步骤；乌方已提交黑海、能源领域专项停火方案并等待俄方回应，俄方在停火问题上仍持谨慎态度，领土与安全保障的核心分歧未解，俄乌互袭仍在继续。',
      /* v351：卡片正面的「第 2 天」原本写死，日期一过就失真。
       * 与 metrics 的 daysSince 同口径按北京日期自动推算（11-10 为第 1 天）。
       * autoProgress 若推进出「停火达成」等真实进展，阶段值优先、天数退到副行。 */
      headline: { label: '会谈进度', value: '第 2 天 · 续谈中', sub: '11-10 首日约 4 小时 · 11-11 继续',
        auto: { type: 'daysSince', date: '2026-11-10',
                template: '第 {n} 天 · {status}',
                sub: '自 11-10 启动 · 已进行 {n} 天',
                terminal: ['停火达成', '首脑会晤', '谈判遇挫', '本轮会谈结束'] } },
      /* v271【撤掉预测市场概率】会谈已经开完首日，「会不会开」的合约已失去意义；
       * 用户口径是「重点在会谈进度」，所以关键指标全部改成已发生事实的量化，
       * 不再挂 Polymarket 合约价（源不可用时也不会再显示陈旧概率）。
       * 「会谈已进行」带 auto 字段，渲染层按日期自动推算，无需人工改。 */
      metrics: [
        { name: '会谈已进行', value: 1, unit: '天', note: '自 11-10 启动 · 按日期自动推算',
          color: '#91a7ff', max: 7, auto: { type: 'daysSince', date: '2026-11-10' } },
        { name: '首日会谈时长', value: 4, unit: '小时', note: '11-10 闭门磋商约 4 小时 · 央视新闻',
          color: '#91a7ff', max: 8 },
        { name: '时隔重启', value: 9, unit: '个月', note: '自 2026-02 谈判停滞 · 时隔 9 个月',
          color: '#ffd43b', max: 12 },
        { name: '参与方', value: 3, unit: '方', note: '俄 / 美 / 乌三方；欧洲代表场外协调未直接入席',
          color: '#91a7ff', max: 4 },
        { name: '布伦特原油地缘溢价', value: 8, note: '美元/桶（估算）· 人工估算', color: '#f783ac' },
        { name: '欧洲天然气（快照）', value: 42, note: '欧元/兆瓦时 · 维护时点值', color: '#91a7ff' }
      ],
      battleground: ['战俘交换', '黑海 / 能源专项停火', '安全保障机制', '欧洲参与谈判'],
      timeline: [
        { t: '11-02', k: 'fact', txt: '来源报道：美方透露将于 11-10~11 在阿布扎比举行俄美乌三方会谈' },
        { t: '11-10', k: 'fact', txt: '首日会谈启动：时隔 9 个月重启面对面磋商，闭门进行约 4 小时；俄方梅金斯基、乌方乌梅罗夫、美方威特科夫与库什纳率团' },
        { t: '11-10', k: 'fact', txt: '泽连斯基称首日会谈「富有建设性和实质意义」，战俘释放为优先事项；三方细化讨论乌安全保障的内容与长期有效机制' },
        { t: '11-10', k: 'fact', txt: '乌外长瑟比加：乌方提交黑海、能源领域专项停火方案，等待俄方回应；并提出推动俄乌总统会晤' },
        { t: '11-10', k: 'fact', txt: '拉夫罗夫：局势不明情况下俄方不会同意再次停火；鲁比奥称会谈「非常富有成效」，应扩大范围让欧洲直接参与' },
        { t: '11-11', k: 'forecast', txt: '关键节点：三方继续会谈；据谈判小组消息，俄代表团将在会谈结束后于阿布扎比向媒体发表声明。领土与安全保障核心分歧仍未解决' }
      ],
      themes: ['停火预期', '战俘与人道', '能源供给', '避险情绪'],
      /* 词表补进本轮会谈的实际人名与短语，保证 11-10/11-11 的稿子能被归集；
       * 不新增「俄罗斯」这类宽词，避免把无关的俄经济稿抓进来。 */
      newsKeys: ['俄乌', '乌克兰', '俄罗斯', '和谈', '停火', '阿布扎比', '威特科夫',
                 '俄美乌', '乌美俄', '三方会谈', '战俘', '库什纳', '梅金斯基',
                 '乌梅罗夫', '拉夫罗夫', '泽连斯基', '瑟比加', '安全保障机制'],
      /* v289【自动更新 · 词表重排】停更根因：旧规则 4 的语序只认「继续会谈」，
       * 而 11-11 实际快讯写的是「三方会谈11日继续」（会谈在前、继续在后），
       * 匹配不到就落到旧规则 5（重启|首日会谈|时隔9个月），被 11-10 的首日稿
       * 永远钉在「首日会谈已结束」。重排为事件逻辑优先级（决定性 > 阶段 > 中性），
       * 并补「会谈N日继续」「第二轮/新一轮」「复会」「谈判破裂」等真实语料语序。
       * neg 是防误判闸门 —— 「不会同意停火」「距离达成还很遥远」不能算达成。
       * 注意：app.js 的阶段推进是「先命中优先级高的规则即锁定」，
       * 时间线（最新进展区）由渲染层按 96h 窗口自动流入，无需人工改。 */
      autoWindowH: 96,
      autoProgress: [
        /* P1 决定性：停火 / 和平协议落地 */
        { re: '达成停火|停火协议|签署停火|正式停火|停战协议|全面停火|和平协议(签署|达成)',
          neg: '不会|未能|尚未|遥不可及|难有|尚无|拒绝|否认|分歧|概率|预期|预测|分析|料将|或于|可能|拟|计划|争取|希望|呼吁|敦促|草案|方案',
          status: '停火达成', statusCls: 'live', value: '停火达成' },
        /* P2 决定性：首脑会晤成行 */
        { re: '领导人会晤|首脑会晤|俄乌总统会晤|总统会晤|普京.{0,8}泽连斯基.{0,8}(会面|会晤|举行|会)',
          neg: '尚未|未能|不会|提议|希望|争取|可能|拟|计划|或于|概率',
          status: '首脑会晤', statusCls: 'live', value: '首脑会晤' },
        /* P3 阶段-负面：谈崩 / 破裂 / 无果 / 无限期推迟 */
        { re: '谈判破裂|会谈破裂|无果而终|不欢而散|未能达成|未达成(任何|共识|协议)|中止谈判|暂停谈判|无限期推迟|谈判无限期|会谈延期|谈判延后|谈判暂停',
          neg: '此前|传闻|若|如果|或将|恐|担忧|担心|风险|尚',
          status: '谈判遇挫', statusCls: 'warn', value: '谈判遇挫' },
        /* P4 阶段-正面：新一轮 / 第二轮会谈开启（含「第一轮结束、第二轮开始」同稿语序）；
         * 「新一轮军事援助 / 制裁」被 neg 挡住，不算会谈轮次 */
        { re: '第二轮|第三轮|新一轮|下一轮|复会|再轮会谈',
          neg: '尚未|不会|未定|传闻|可能|或于|援助|军援|武器|制裁|军售',
          status: '会谈进行中', statusCls: 'live', value: '新一轮会谈' },
        /* P5 阶段：本轮休会 / 结束（「将在会谈结束后发表声明」被 neg 挡住，不算结束） */
        { re: '休会|闭会|会谈结束|磋商结束|谈判结束|结束本轮|本轮结束',
          neg: '将|拟|或|尚未',
          status: '本轮结束', statusCls: 'watch', value: '本轮会谈结束' },
        /* P6 中性：续谈 / 磋商中 —— 覆盖「会谈11日继续」这类倒装语序；
         * 停火方案、战俘、安全保障等议题磋商也归到「进行中」而非基线。
         * 注意不放裸「三方会谈」这类同义名词，否则「三方会谈重启」会被抢判。 */
        { re: '继续(举行)?(会谈|磋商|谈判)|继续.{0,6}(举行|磋商|会谈|谈判)|会谈.{0,8}继续|磋商.{0,8}继续|继续磋商|续谈|会谈进行|磋商中|谈判中|次日|第\\s*[2-9]\\s*天|停火方案|战俘|换俘|交换被俘|安全保障|达成共识|共识|联合声明',
          neg: '尚未|未能|不会|拒绝',
          status: '会谈进行中', statusCls: 'live', value: '续谈 · 议题磋商' },
        /* v291【删基线规则】原 P7「重启|首日|启动」会把 11-10 首日旧稿永久钉成
         * 「首日会谈已结束」，并覆盖 events.js 里已更新的静态基线（用户实测清缓存无效）。
         * 基线不再由规则决定：没有新阶段命中时直接用本卡静态 status/headline，
         * 规则只允许「向前推进」。 */
      ],
      /* v271【相关资讯兜底】实时快讯源受 CORS / 代理影响可能一条都抓不到，
       * 所以内置逐条核实的会谈资讯（只标日期，不编造具体分钟）。
       * 实时源有同类新闻时按标题去重，实时优先；源恢复后新进展自动顶上来。 */
      seedNews: [
        { id: 'ukr-seed-1', dateLabel: '11-11', level: 'A', cat: '地缘',
          title: '俄美乌三方会谈11日继续，俄代表团将于会谈结束后在阿布扎比向媒体发表声明',
          src: '央视新闻 / 卫星通讯社' },
        { id: 'ukr-seed-2', dateLabel: '11-11', level: 'A', cat: '地缘',
          title: '俄美乌时隔9个月再谈：首日近4小时，领土与安全保障核心分歧未解，俄乌互袭不止',
          src: '中国经济网 / 澎湃新闻' },
        { id: 'ukr-seed-3', dateLabel: '11-10', level: 'A', cat: '地缘',
          title: '泽连斯基称阿布扎比首日会谈富有建设性，战俘释放为优先讨论事项',
          src: '央视新闻' },
        { id: 'ukr-seed-4', dateLabel: '11-10', level: 'A', cat: '地缘',
          title: '乌方提交黑海、能源领域专项停火方案，等待俄方回应',
          src: '央视新闻 / 极目新闻' },
        { id: 'ukr-seed-5', dateLabel: '11-10', level: 'A', cat: '地缘',
          title: '拉夫罗夫：俄乌局势不明情况下俄方不会同意再次停火',
          src: '央视新闻' },
        { id: 'ukr-seed-6', dateLabel: '11-10', level: 'A', cat: '地缘',
          title: '鲁比奥称三方会谈非常富有成效，应扩大范围让欧洲直接参与谈判',
          src: '央视新闻' }
      ],
      assets: ['BRENT', 'GOLD', 'EURUSD'],
      src: '央视新闻 / 新华社 / 中国经济网 / 澎湃新闻 / 卫星通讯社（11-10 ~ 11-11）'
    },
    {
      id: 'apec',
      icon: '🌐',
      title: 'APEC 领导人会议',
      phase: '会议进行中',   /* 大字只显示稳定阶段短语，详细态势放简介小字 */
      cat: '国际 · 亚太',
      status: '进行中',
      statusCls: 'live',
      hot: 3,
      updated: '2026-11-18 16:53',
      summary: '第33次APEC领导人非正式会议11-18至19在深圳举行，中国时隔12年第三次主办；19日中俄美元首将历史首次三边会晤。',
      headline: { label: '会议态势', value: '会议进行中', sub: 'LLM 态势演进' },
      metrics: [
        { name: '中国年经贸成果', value: 50, unit: '项', note: '近50项务实成果 · 数智领域占半壁', color: '#8ce99a' },
        { name: 'ABAC 成果文件', value: 14, unit: '项', note: '历年新高 · 工商咨询理事会', color: '#8ce99a' }
      ],
      timeline: [
        { t: '11-14~16', k: 'fact', txt: 'APEC工商咨询理事会第四次会议在深圳举行' },
        { t: '11-17', k: 'fact', txt: '习近平在深圳会见越南苏林、巴新总理马拉佩' },
        { t: '11-18~19', k: 'fact', txt: '第33次领导人非正式会议在深圳举行' },
        { t: '11-19', k: 'forecast', txt: '中俄美元首历史首次三边面对面会晤' }
      ],
      newsKeys: ['APEC', '亚太经合', '领导人会议', '深圳', '普京', '特朗普', '自贸区'],
      newsMust: ['APEC', '亚太经合', '领导人会议', '中俄美', '自贸区', '深圳'],
      newsExcl: ['APEC历史', '文旅', '安保管制'],
      assets: ['HS300', 'HSI', 'USDCNH'],
      src: '新华社 / 央视新闻 / 外交部 / 中国经济网（11-16 ~ 11-18）'
    },
    {
      id: 'huang',
      icon: '🧑‍💼',
      title: '黄仁勋事件',
      phase: '跟踪中',
      cat: '公司 · 科技',
      status: '跟踪中',
      statusCls: 'watch',
      hot: 2,
      updated: '2026-11-20 19:50',
      summary: '跟踪黄仁勋相关调查后续、本人动向与英伟达CEO接任安排，等待权威媒体与官方声明确认实质进展。',
      headline: { label: '英伟达 NVDA', value: '', sub: '关联走势 · 实时', asset: 'NVDA' },
      metrics: [
        { name: '英伟达股价（NVDA）', note: '关联标的 · 实时报价', color: '#63e6be', asset: 'NVDA' },
        { name: '费城半导体指数（SOX）', note: '关联板块 · 实时报价', color: '#8ce99a', asset: 'SOX' },
        { name: '年底前官宣新CEO', value: 14.5, unit: '%',
          note: 'Polymarket：New NVIDIA CEO named by Dec 31, 2026', color: '#ffd43b',
          pred: { q: 'New NVIDIA CEO named by December 31 2026',
                  event: 'New NVIDIA CEO named by December 31, 2026',
                  market: 'New NVIDIA CEO named', outcome: 'Yes' } },
        { name: '下任CEO为外部人选', value: 59.5, unit: '%',
          note: 'Polymarket：Next NVIDIA CEO - external candidate', color: '#ffd43b',
          pred: { q: 'Next NVIDIA CEO internal or external',
                  event: 'Next NVIDIA CEO: internal or external',
                  market: 'external candidate', outcome: 'Yes' } },
        { name: '年底前回归英伟达', value: 5.6, unit: '%',
          note: 'Polymarket：Will he rejoin NVIDIA by Dec 31, 2026', color: '#ffd43b',
          pred: { q: 'rejoins NVIDIA by December 31 2026',
                  event: 'rejoins NVIDIA by',
                  market: 'December 31, 2026', outcome: 'Yes' } }
      ],
      timeline: [
        { t: '11-20', k: 'fact', txt: '英伟达管理层动向与CEO接任安排受市场关注' }
      ],
      newsKeys: ['黄仁勋', '英伟达', 'NVIDIA', 'Jensen', 'CEO接任'],
      newsMust: ['黄仁勋', '英伟达', 'NVIDIA', 'CEO接任'],
      newsExcl: ['GPU发布', '显卡', '财报'],
      assets: ['NVDA', 'SOX'],
      src: '权威媒体 / 公司公告（持续跟踪）'
    }
  ];

  /* 事件 <> 快讯 的匹配判定，抽成纯函数放在数据层，好处：
   *   ① 前端只传标题，不用把匹配规则散在渲染逻辑里；
   *   ② 可以单测（tools/test_events_match.js），改词表不会静默回归。
   * 三层：newsExcl 一票否决 → newsKeys 至少命中一个 → newsMust 必须再命中其一。
   * 只匹配标题，不碰正文，避免正文里顺带提到就被抓。 */
  w.evMatchNews = function (ev, title) {
    if (!ev) return false;
    var t = String(title || '').toLowerCase();
    if (!t) return false;
    function any(list) {
      list = list || [];
      for (var i = 0; i < list.length; i++) {
        if (t.indexOf(String(list[i]).toLowerCase()) >= 0) return true;
      }
      return false;
    }
    if (ev.newsExcl && any(ev.newsExcl)) return false;
    if (!any(ev.newsKeys)) return false;
    if (ev.newsMust && ev.newsMust.length && !any(ev.newsMust)) return false;
    return true;
  };
})(window);

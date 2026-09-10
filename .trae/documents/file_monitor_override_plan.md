# 文件监控重复文件名处理方案

## 问题分析

当前 `DLM5000FileMonitor` 模块的实现中，对于重复文件名的处理存在以下问题：

1. **自动检测逻辑**：只在文件名变化时自动触发记录
2. **覆盖保存场景**：当测试员覆盖保存同名文件时，文件名不变，自动检测无法触发
3. **手动触发**：需要测试员手动调用 `record_now()` 方法来记录覆盖保存的数据

## 技术方案

### 1. 增强监控逻辑

修改 `_monitor_loop` 方法，增加对数据变化的检测：

- **轻量轮询**：继续使用文件名 + CH1 MAX 的快速检测
- **稳定性判断**：当数据连续两次相同视为稳定
- **数据变化检测**：当检测到数据从稳定状态变为新的稳定状态时，即使文件名不变，也触发记录

### 2. 实现步骤

1. **修改监控循环**：在 `_monitor_loop` 方法中，增加对数据变化的检测逻辑
2. **更新触发条件**：当数据稳定且与之前的稳定状态不同时，触发记录
3. **保持兼容性**：确保修改后的逻辑与原有的文件名变化检测逻辑兼容

### 3. 具体修改点

- **文件**：`test1/file_monitor.py`
- **方法**：`_monitor_loop`
- **修改内容**：
  - 增加对数据稳定性的判断
  - 当数据从稳定状态变为新的稳定状态时，触发记录
  - 确保即使文件名不变，也能检测到数据变化并触发记录

## 预期效果

1. **自动检测覆盖保存**：当测试员覆盖保存同名文件时，系统能够自动检测到数据变化并触发记录
2. **无需手动触发**：测试员无需手动调用 `record_now()` 方法
3. **保持性能**：修改后的逻辑仍然保持轻量，不会增加系统负担
4. **向后兼容**：修改后的逻辑与原有的文件名变化检测逻辑兼容

## 风险评估

1. **误触发风险**：可能会在数据自然波动时误触发记录
   - 缓解措施：使用连续两次相同的数据作为稳定状态的判断条件

2. **性能影响**：增加数据变化检测可能会增加系统负担
   - 缓解措施：继续使用轻量的 CH1 MAX 作为快速检测指标，只在需要时才读取全部测量值

3. **兼容性问题**：修改后的逻辑可能与现有的使用方式不兼容
   - 缓解措施：保持原有的 `record_now()` 方法，确保手动触发功能仍然可用

## 测试验证

1. **测试场景**：
   - 覆盖保存同名文件，验证是否自动触发记录
   - 正常保存新文件名，验证是否正常触发记录
   - 数据自然波动，验证是否不会误触发记录

2. **测试步骤**：
   - 启动文件监控
   - 保存一个文件，验证记录触发
   - 覆盖保存同名文件，验证记录触发
   - 保存另一个新文件，验证记录触发
   - 观察系统运行状态，确保无异常

## 实现代码

### 修改 `_monitor_loop` 方法

```python
def _monitor_loop(self):
    """
    后台监控主循环。

    工作流：Run → Stop → 保存 → Run
    - 平时只轮询文件名 + CH1 MAX（2次 read，轻量）
    - Run 时数据持续变化 → 忽略
    - Stop 后数据稳定（连续2次相同）→ 记录基准
    - 再次 Stop 且数据与基准不同 → 读取全部测量值并记录
    - 文件名变化 → 记录
    """
    logger = self._get_logger()
    logger.info(f"文件监控已启动，轮询间隔 {self._poll_interval}s")

    last_quick_hash = ""   # 上次快速探测值（文件名+CH1_MAX）
    stable_quick_hash = "" # 最近稳定时的快速探测值
    is_stable = False

    while self._running:
        try:
            # 1. 轻量轮询：文件名 + CH1 MAX（共 2 次 read）
            current_name = self._poll_filename()
            if current_name is None:
                time.sleep(self._poll_interval)
                continue

            quick_val = self._poll_single_channel(1)
            quick_hash = f"{current_name}|{quick_val}"

            # 2. 判断稳定性
            just_became_stable = False
            if quick_hash == last_quick_hash and last_quick_hash != "":
                if not is_stable:
                    is_stable = True
                    just_became_stable = True
            else:
                is_stable = False

            # 3. 判断是否触发记录
            should_record = False

            if self._last_filename == "":
                # 首次：记录基准
                logger.info(f"初始: 文件={current_name}")
                self._last_filename = current_name
                stable_quick_hash = quick_hash

            elif is_stable and just_became_stable:
                if quick_hash != stable_quick_hash and stable_quick_hash != "":
                    # 数据稳定且与之前不同 → 新的 Stop 或覆盖保存
                    logger.info(f"检测到新数据: 文件={current_name}")
                    should_record = True
                stable_quick_hash = quick_hash

            elif is_stable and current_name != self._last_filename:
                logger.info(
                    f"检测到新文件: "
                    f"'{self._last_filename}' → '{current_name}'"
                )
                should_record = True

            # 4. 记录时才读取全部测量值（重连后 20 次 read）
            if should_record:
                self._do_record(current_name)
                self._last_filename = current_name
                stable_quick_hash = quick_hash

            last_quick_hash = quick_hash

        except Exception as e:
            logger.error(f"监控循环异常: {e}")

        time.sleep(self._poll_interval)

    logger.info("文件监控已停止")
```

## 总结

通过修改 `file_monitor.py` 中的 `_monitor_loop` 方法，增加对数据变化的检测逻辑，系统能够在覆盖保存同名文件时自动触发记录，无需测试员手动调用 `record_now()` 方法。修改后的逻辑保持轻量，不会增加系统负担，同时与原有的文件名变化检测逻辑兼容。
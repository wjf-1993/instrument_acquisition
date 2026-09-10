# 仪器数据采集系统 - 待办事项与配置指引

## 待办事项

### 高优先级

1. **实现采集调度器**
   - 创建 `acquisition_scheduler.py`
   - 实现并发采集和时间戳同步
   - 支持同步和顺序两种采集模式

2. **实现仪器适配器**
   - 创建 `adapters/__init__.py` 工厂函数
   - 实现 `dlm5000_adapter.py`（SCPI/VISA）
   - 实现 `wt3000_adapter.py`（Socket）
   - 实现 `wt1804e_adapter.py`（SCPI/VISA）

3. **实现配置模块**
   - 创建 `config.py` 配置解析模块
   - 创建 `config.yaml` 配置文件
   - 支持运行时配置更新

4. **实现日志模块**
   - 创建 `logger.py` 统一日志管理
   - 支持控制台和文件日志
   - 支持不同级别的日志

5. **实现主调度模块**
   - 创建 `main.py` 主调度入口
   - 支持命令行参数解析
   - 提供演示模式

### 中优先级

1. **增强可视化功能**
   - 创建 `visualization.py` 可视化模块
   - 使用 PySide6 实现GUI界面
   - 支持实时数据展示
   - 支持多种图表类型

2. **完善报告模板**
   - 设计更多Excel报告模板
   - 支持自定义报告格式
   - 添加图表和数据分析功能

3. **添加系统监控**
   - 实现系统运行状态监控
   - 实现告警机制
   - 提供远程监控接口

### 低优先级

1. **性能优化**
   - 优化数据库查询性能
   - 优化网络传输效率
   - 优化内存使用

2. **扩展支持的仪器类型**
   - 添加更多型号的示波器
   - 添加更多型号的功率分析仪
   - 支持其他类型的仪器（如信号发生器）

3. **完善文档**
   - 编写详细的用户手册
   - 提供API文档
   - 创建示例代码

## 配置指引

### 环境配置

1. **Python 环境**
   - Python 3.10+
   - 推荐使用虚拟环境

2. **依赖库安装**
   ```bash
   pip install pyyaml pyvisa pyvisa-py openpyxl
   ```

3. **仪器连接配置**
   - **DLM5000示波器**：通过VISA接口连接
   - **WT3000功率分析仪**：通过Socket接口连接
   - **WT1804E功率分析仪**：通过VISA接口连接

### 配置文件说明

**config.yaml 示例**：

```yaml
# 仪器配置
instruments:
  dlm5000:
    type: oscilloscope
    connection: visa
    resource: TCPIP0::192.168.1.100::INSTR
    timeout: 10
  wt3000:
    type: power_analyzer
    connection: socket
    host: 192.168.1.101
    port: 5025
    timeout: 10
  wt1804e:
    type: power_analyzer
    connection: visa
    resource: TCPIP0::192.168.1.102::INSTR
    timeout: 10

# 采集配置
acquisition:
  mode: sync  # sync 或 sequential
  interval: 1.0  # 采集间隔（秒）
  max_retries: 3  # 失败重试次数
  retry_delay: 1.0  # 重试间隔（秒）

# 存储配置
storage:
  db_path: data/acquisition.db
  backup_enabled: true
  backup_interval: 3600  # 备份间隔（秒）
  retention_days: 30  # 数据保留天数

# 报告配置
report:
  template_dir: templates
  output_dir: reports
  default_template: default_report_template.xlsx
  auto_generate: false
  generate_interval: 3600  # 自动生成间隔（秒）
```

### 运行指引

1. **演示模式**
   ```bash
   python main.py --demo
   ```

2. **单次采集**
   ```bash
   python main.py --once
   ```

3. **持续采集**
   ```bash
   python main.py
   ```

4. **查看已配置仪器**
   ```bash
   python main.py --list-instruments
   ```

5. **生成报告**
   ```bash
   python main.py --generate-report --start-time 1620000000 --end-time 1620086400
   ```

### 故障排除

1. **仪器连接失败**
   - 检查网络连接
   - 验证仪器IP地址和端口
   - 确认仪器电源已开启
   - 检查VISA驱动是否安装正确

2. **数据存储失败**
   - 检查数据库文件权限
   - 验证磁盘空间是否充足
   - 检查SQLite数据库是否损坏

3. **报告生成失败**
   - 检查openpyxl库是否安装
   - 验证模板文件是否存在
   - 检查输出目录权限

4. **性能问题**
   - 减少采集间隔
   - 降低波形数据点数量
   - 定期清理旧数据
   - 优化数据库索引

## 部署建议

1. **生产环境部署**
   - 使用系统服务运行（如systemd或Windows服务）
   - 配置自动启动
   - 设置定期备份

2. **监控与维护**
   - 定期检查系统日志
   - 监控磁盘空间使用情况
   - 定期清理过期数据
   - 备份配置文件和数据库

3. **扩展性考虑**
   - 设计模块化架构，便于添加新功能
   - 使用配置文件管理参数，避免硬编码
   - 提供API接口，便于与其他系统集成

## 安全注意事项

1. **网络安全**
   - 限制仪器网络访问权限
   - 使用防火墙保护仪器网络
   - 避免在公共网络中传输敏感数据

2. **数据安全**
   - 定期备份数据库
   - 加密存储敏感数据
   - 限制数据库文件访问权限

3. **系统安全**
   - 定期更新依赖库
   - 避免使用默认密码
   - 限制系统访问权限

## 版本管理

- **v1.0**：基础功能实现（数据采集、存储、报告生成）
- **v1.1**：添加可视化功能
- **v1.2**：增强报告模板
- **v1.3**：添加系统监控
- **v2.0**：扩展支持更多仪器类型

## 联系方式

- **维护人员**：系统管理员
- **技术支持**：技术支持团队
- **文档更新**：定期更新文档
- **问题反馈**：通过项目管理系统提交问题
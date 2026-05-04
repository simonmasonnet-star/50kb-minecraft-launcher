# LiteLauncher

LiteLauncher 是一个轻量级、高度可定制的 Minecraft 启动器，使用 Python 编写，旨在提供简洁、高效的游戏启动体验。

## 特性

- 利用多线程技术并行下载游戏库文件与资源文件。
- 支持离线登录   微软账号 (MSA) OAuth2 验证登录出了点问题
- 支持配置和切换多个 Java 运行环境 (JDK)。
- 自动处理游戏版本依赖、EULA 同意及 JVM 参数生成。
- 在 Windows上测试，运行正常

### 环境准备
- 确保已安装 Python 3.8+。
- 准备好至少一个可用的 Java 环境 (JDK 8/17/21，取决于你要运行的版本)。

### 运行
1. 克隆代码或下载 `launcher.py`。
2. 在终端/命令行中运行：
   ```bash
   python launcher.py
   # LiteLauncher

[English]

### English
**LiteLauncher** is a lightweight, highly customizable Minecraft launcher written in Python, designed to provide a clean and efficient game-launching experience.

#### Features
- Leverages multi-threading to parallelize the downloading of game libraries and assets.
- Supports both offline usernames and Microsoft Account (MSA) OAuth2 device code authentication.
- Easily configure and switch between multiple Java Runtime Environments (JRE/JDK).
- Automatically handles game directory structures, EULA acceptance, and JVM argument generation.
  test on Windows

#### Quick Start
1. Ensure you have **Python 3.8+** installed.
2. Clone this repository or download `launcher.py`.
3. Run the following command in your terminal:
   ```bash
   python launcher.py

import numpy as np
import matplotlib.pyplot as plt

def softmax(x, temperature=1.0):
    """Softmax 函数"""
    x = x / temperature
    exp_x = np.exp(x - np.max(x))  # 数值稳定性
    return exp_x / np.sum(exp_x)

# 创建输入数据
x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
temperatures = [0.5, 1.0, 2.0, 5.0]

# 创建图表
plt.figure(figsize=(12, 8))

# 子图1: 不同温度下的 softmax 输出
plt.subplot(2, 2, 1)
for temp in temperatures:
    probs = softmax(x, temperature=temp)
    plt.plot(range(len(x)), probs, marker='o', label=f'T={temp}')
plt.xlabel('Input Index')
plt.ylabel('Probability')
plt.title('Softmax Output at Different Temperatures')
plt.legend()
plt.grid(True, alpha=0.3)

# 子图2: 连续输入的 softmax
plt.subplot(2, 2, 2)
x_continuous = np.linspace(-5, 5, 100)
for temp in temperatures:
    # 对每个点计算 softmax（相对于其他点）
    probs = []
    for i, val in enumerate(x_continuous):
        # 创建一个包含当前值和其他固定值的向量
        vec = np.array([val, 0, 0])
        probs.append(softmax(vec, temperature=temp)[0])
    plt.plot(x_continuous, probs, label=f'T={temp}')
plt.xlabel('Input Value')
plt.ylabel('Probability')
plt.title('Softmax Probability for Single Input')
plt.legend()
plt.grid(True, alpha=0.3)

# 子图3: 2D 输入空间的 softmax 决策边界
plt.subplot(2, 2, 3)
x1 = np.linspace(-3, 3, 100)
x2 = np.linspace(-3, 3, 100)
X1, X2 = np.meshgrid(x1, x2)
Z = np.zeros_like(X1)

for i in range(X1.shape[0]):
    for j in range(X1.shape[1]):
        vec = np.array([X1[i, j], X2[i, j]])
        probs = softmax(vec, temperature=1.0)
        Z[i, j] = probs[0]  # 第一个类别的概率

plt.contourf(X1, X2, Z, levels=20, cmap='RdYlBu')
plt.colorbar(label='P(class 1)')
plt.xlabel('x1')
plt.ylabel('x2')
plt.title('2D Softmax Decision Boundary (T=1.0)')

# 子图4: 温度对分布锐度的影响
plt.subplot(2, 2, 4)
x_sharp = np.array([1.0, 2.0, 3.0])
entropy_values = []
for temp in np.linspace(0.1, 5, 50):
    probs = softmax(x_sharp, temperature=temp)
    entropy = -np.sum(probs * np.log(probs + 1e-10))
    entropy_values.append(entropy)

plt.plot(np.linspace(0.1, 5, 50), entropy_values)
plt.xlabel('Temperature')
plt.ylabel('Entropy')
plt.title('Entropy vs Temperature')
plt.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('softmax_visualization.png', dpi=150, bbox_inches='tight')
print("图像已保存为 softmax_visualization.png")
plt.show()
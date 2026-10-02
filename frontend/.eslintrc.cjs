module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', '.eslintrc.cjs'],
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  plugins: ['react-refresh', '@typescript-eslint'],
  rules: {
    // P3-3: 项目使用自定义 Hook + Provider 组件同文件导出的标准 React Context 模式，
    // 该规则误报较多，关闭以减少噪音；关键依赖警告保持开启。
    'react-refresh/only-export-components': 'off',
    '@typescript-eslint/no-explicit-any': 'off',
    // P3-3: 允许 while(true)/for(;;) 这类读取流的标准循环写法
    'no-constant-condition': ['error', { checkLoops: false }],
    // P3-3: 允许 _ 前缀的未使用参数/变量，常见于类型守卫和回调占位
    '@typescript-eslint/no-unused-vars': [
      'error',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
  },
}
